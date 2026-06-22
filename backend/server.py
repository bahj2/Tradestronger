from fastapi import FastAPI, APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import json
import asyncio
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
import httpx
import feedparser
from emergentintegrations.llm.chat import LlmChat, UserMessage

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# Env
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
TWELVEDATA_API_KEY = os.environ.get('TWELVEDATA_API_KEY', '')
EMERGENT_LLM_KEY = os.environ.get('EMERGENT_LLM_KEY', '')

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

app = FastAPI(title="XAUUSD Trading Analytics API")
api_router = APIRouter(prefix="/api")

# In-memory caches (short TTL) to respect TwelveData rate limits
_cache: Dict[str, Dict[str, Any]] = {}

def _cache_get(key: str, ttl_seconds: int) -> Optional[Any]:
    entry = _cache.get(key)
    if not entry:
        return None
    if (datetime.now(timezone.utc) - entry['ts']).total_seconds() > ttl_seconds:
        return None
    return entry['data']

def _cache_get_stale(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    return entry['data'] if entry else None

def _cache_set(key: str, data: Any) -> None:
    _cache[key] = {'ts': datetime.now(timezone.utc), 'data': data}

TD_BASE = "https://api.twelvedata.com"
SYMBOL = "XAU/USD"

# ------------------- TwelveData helpers -------------------
async def td_get(path: str, params: dict) -> dict:
    params = {**params, 'apikey': TWELVEDATA_API_KEY}
    async with httpx.AsyncClient(timeout=15.0) as http:
        try:
            r = await http.get(f"{TD_BASE}/{path}", params=params)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                raise HTTPException(status_code=429, detail="Market data rate-limited. Try again in a moment.")
            raise HTTPException(status_code=502, detail=f"Market data HTTP {status}")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=503, detail=f"Market data unavailable: {str(e)[:120]}")
        data = r.json()
        if isinstance(data, dict) and data.get('status') == 'error':
            code = data.get('code', 502)
            if code == 429:
                raise HTTPException(status_code=429, detail="Market data rate-limited. Try again in a moment.")
            raise HTTPException(status_code=502, detail=f"Market data error: {data.get('message', 'unknown')}")
        return data

# ------------------- Technical Indicators -------------------
def sma(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    s = sum(values[:period])
    out[period - 1] = s / period
    for i in range(period, len(values)):
        s += values[i] - values[i - period]
        out[i] = s / period
    return out

def ema(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out

def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    avg_g = gains / period
    avg_l = losses / period
    rs = (avg_g / avg_l) if avg_l else 100
    out[period] = 100 - 100 / (1 + rs) if avg_l else 100
    for i in range(period + 1, len(values)):
        ch = values[i] - values[i - 1]
        g = max(ch, 0)
        ls = max(-ch, 0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + ls) / period
        rs = (avg_g / avg_l) if avg_l else 100
        out[i] = 100 - 100 / (1 + rs) if avg_l else 100
    return out

def macd(values: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    # signal EMA over non-None macd
    valid = [m for m in macd_line if m is not None]
    signal_vals = ema(valid, signal) if len(valid) >= signal else []
    # align signal back to original length
    signal_line: List[Optional[float]] = [None] * len(values)
    offset = next((i for i, v in enumerate(macd_line) if v is not None), 0)
    for i, v in enumerate(signal_vals):
        signal_line[offset + i] = v
    hist = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist

def bollinger(values: List[float], period: int = 20, mult: float = 2.0):
    mid = sma(values, period)
    upper: List[Optional[float]] = [None] * len(values)
    lower: List[Optional[float]] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        sd = var ** 0.5
        upper[i] = mean + mult * sd
        lower[i] = mean - mult * sd
    return mid, upper, lower

# ------------------- Models -------------------
class Quote(BaseModel):
    symbol: str
    price: float
    change: float
    percent_change: float
    high: float
    low: float
    open: float
    previous_close: float
    timestamp: str

class Candle(BaseModel):
    time: int  # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None

class IndicatorSnapshot(BaseModel):
    rsi: Optional[float]
    macd: Optional[float]
    macd_signal: Optional[float]
    macd_hist: Optional[float]
    ema20: Optional[float]
    ema50: Optional[float]
    ema200: Optional[float]
    bb_upper: Optional[float]
    bb_mid: Optional[float]
    bb_lower: Optional[float]
    price: float

class AIVote(BaseModel):
    model: str  # human label e.g. "Claude Sonnet 4.5"
    model_id: str  # short id e.g. "claude" / "gpt5"
    action: str  # BUY / SELL / HOLD
    confidence: int
    summary: str
    available: bool = True


class AISignal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: str  # BUY / SELL / HOLD
    confidence: int  # 0-100
    entry: float
    stop_loss: float
    take_profit: float
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    timeframe: str
    reasoning: str
    summary: str
    sentiment: str  # BULLISH / BEARISH / NEUTRAL
    indicators: Dict[str, Optional[float]]
    created_at: str
    # Strong-analysis extensions
    signal_strength: Optional[str] = None  # WEAK / MODERATE / STRONG / VERY_STRONG
    valid_for_minutes: Optional[int] = None
    expires_at: Optional[str] = None
    risk_reward: Optional[float] = None
    higher_tf_trend: Optional[str] = None
    lower_tf_momentum: Optional[str] = None
    news_sentiment: Optional[str] = None
    dxy_price: Optional[float] = None
    dxy_change_pct: Optional[float] = None
    analysis_steps: Optional[Dict[str, str]] = None
    # Ensemble fields
    consensus: Optional[str] = None  # DOUBLE_CONFIRMED / MAJORITY / CONFLICTED / SINGLE_SOURCE
    votes: Optional[List[AIVote]] = None
    news_sources: Optional[List[Dict[str, str]]] = None  # headlines used in analysis

# ------------------- Routes -------------------
@api_router.get("/")
async def root():
    return {"service": "XAUUSD Trading Analytics API", "status": "ok"}

@api_router.get("/market/quote", response_model=Quote)
async def get_quote():
    cache = _cache_get('quote', ttl_seconds=15)
    if cache:
        return cache
    try:
        data = await td_get("quote", {"symbol": SYMBOL})
    except HTTPException as e:
        stale = _cache_get_stale('quote')
        if stale and e.status_code in (429, 502, 503):
            return stale
        raise
    q = Quote(
        symbol=data.get('symbol', SYMBOL),
        price=float(data['close']),
        change=float(data.get('change', 0)),
        percent_change=float(data.get('percent_change', 0)),
        high=float(data.get('high', data['close'])),
        low=float(data.get('low', data['close'])),
        open=float(data.get('open', data['close'])),
        previous_close=float(data.get('previous_close', data['close'])),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    _cache_set('quote', q.model_dump())
    return q

@api_router.get("/market/candles")
async def get_candles(interval: str = Query("1h"), size: int = Query(120, ge=20, le=500)):
    key = f"candles:{interval}:{size}"
    cache = _cache_get(key, ttl_seconds=30)
    if cache:
        return cache
    try:
        data = await td_get("time_series", {"symbol": SYMBOL, "interval": interval, "outputsize": size})
    except HTTPException as e:
        stale = _cache_get_stale(key)
        if stale and e.status_code in (429, 502, 503):
            return stale
        raise
    values = data.get('values', [])
    # TwelveData returns latest first; reverse for chronological
    candles = []
    for v in reversed(values):
        try:
            t = datetime.fromisoformat(v['datetime'].replace(' ', 'T'))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            candles.append({
                'time': int(t.timestamp()),
                'open': float(v['open']),
                'high': float(v['high']),
                'low': float(v['low']),
                'close': float(v['close']),
                'volume': float(v.get('volume') or 0),
            })
        except (KeyError, ValueError):
            continue
    result = {'symbol': SYMBOL, 'interval': interval, 'candles': candles}
    _cache_set(key, result)
    return result

@api_router.get("/analysis/indicators", response_model=IndicatorSnapshot)
async def get_indicators(interval: str = Query("1h")):
    candles_resp = await get_candles(interval=interval, size=250)
    closes = [c['close'] for c in candles_resp['candles']]
    if len(closes) < 30:
        raise HTTPException(status_code=400, detail="Not enough data for indicators")
    rsi_v = rsi(closes, 14)
    macd_line, sig_line, hist = macd(closes)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    mid, upper, lower = bollinger(closes, 20, 2.0)
    return IndicatorSnapshot(
        rsi=rsi_v[-1],
        macd=macd_line[-1],
        macd_signal=sig_line[-1],
        macd_hist=hist[-1],
        ema20=ema20[-1],
        ema50=ema50[-1],
        ema200=ema200[-1],
        bb_upper=upper[-1],
        bb_mid=mid[-1],
        bb_lower=lower[-1],
        price=closes[-1],
    )

TIMEFRAME_HIERARCHY = {
    "1min": ("5min", "15min"),
    "5min": ("15min", "1h"),
    "15min": ("1h", "4h"),
    "1h": ("4h", "1day"),
    "4h": ("1day", "1week"),
    "1day": ("1week", "1month"),
}

TIMEFRAME_EXPIRY_MIN = {
    "1min": 30,
    "5min": 120,
    "15min": 360,
    "1h": 720,
    "4h": 2880,
    "1day": 10080,
}

async def _safe_indicators(interval: str) -> Optional[IndicatorSnapshot]:
    try:
        return await get_indicators(interval=interval)
    except Exception:
        return None

async def _safe_dxy() -> Optional[Dict[str, float]]:
    cached = _cache_get('dxy', ttl_seconds=300)
    if cached:
        return cached
    # Primary: Yahoo Finance (free, no key needed)
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB"
        async with httpx.AsyncClient(
            timeout=10.0,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        ) as http:
            r = await http.get(url, params={'interval': '1d', 'range': '5d'})
            r.raise_for_status()
            data = r.json()
            result = data['chart']['result'][0]
            meta = result['meta']
            price = float(meta['regularMarketPrice'])
            prev = float(
                meta.get('chartPreviousClose')
                or meta.get('previousClose')
                or price
            )
            pct = ((price - prev) / prev * 100) if prev else 0.0
            dxy = {'price': price, 'percent_change': pct}
            _cache_set('dxy', dxy)
            return dxy
    except Exception as e:
        logging.warning(f"Yahoo DXY fetch failed: {e}")
    # Fallback: TwelveData (often restricted on free tier)
    try:
        data = await td_get("quote", {"symbol": "DXY"})
        dxy = {
            'price': float(data['close']),
            'percent_change': float(data.get('percent_change', 0)),
        }
        _cache_set('dxy', dxy)
        return dxy
    except Exception:
        return None

async def _safe_news_titles(limit: int = 8) -> List[str]:
    items = await _fetch_all_news()
    return [item.get('title', '') for item in items[:limit] if item.get('title')]

async def _safe_news_items(limit: int = 12) -> List[dict]:
    items = await _fetch_all_news()
    return items[:limit]

def _trend_label(snap: Optional[IndicatorSnapshot]) -> str:
    if not snap or snap.ema20 is None or snap.ema50 is None:
        return "UNKNOWN"
    if snap.price > snap.ema20 > snap.ema50:
        return "BULLISH"
    if snap.price < snap.ema20 < snap.ema50:
        return "BEARISH"
    return "MIXED"

def _build_strong_prompt(
    snapshot: IndicatorSnapshot,
    interval: str,
    recent_candles: List[dict],
    higher_snap: Optional[IndicatorSnapshot],
    lower_snap: Optional[IndicatorSnapshot],
    higher_tf: str,
    lower_tf: str,
    dxy: Optional[Dict[str, float]],
    news_titles: List[str],
) -> str:
    def f(v):
        return f"{v:.4f}" if isinstance(v, (int, float)) else "n/a"

    last10 = recent_candles[-10:]
    candle_str = "\n".join(
        f"  {c['open']:.2f} O / {c['high']:.2f} H / {c['low']:.2f} L / {c['close']:.2f} C"
        for c in last10
    )

    def tf_block(label, snap):
        if not snap:
            return f"  [{label}]: unavailable"
        return (
            f"  [{label}] price {snap.price:.2f} | EMA20 {f(snap.ema20)} | "
            f"EMA50 {f(snap.ema50)} | EMA200 {f(snap.ema200)} | RSI {f(snap.rsi)} | "
            f"MACD {f(snap.macd)} (sig {f(snap.macd_signal)}) | trend {_trend_label(snap)}"
        )

    mtf_section = "\n".join([
        tf_block(f"LOWER · {lower_tf}", lower_snap),
        tf_block(f"PRIMARY · {interval}", snapshot),
        tf_block(f"HIGHER · {higher_tf}", higher_snap),
    ])

    dxy_section = (
        f"DXY (US Dollar Index): {dxy['price']:.2f} ({dxy['percent_change']:+.2f}% today). "
        f"Remember: gold is INVERSELY correlated with DXY — strong USD usually weighs on gold."
        if dxy else "DXY: unavailable — use neutral assumption."
    )

    news_section = (
        "\n".join(f"  - {t}" for t in news_titles[:8])
        if news_titles else "  (no recent gold news fetched)"
    )

    expiry_floor = TIMEFRAME_EXPIRY_MIN.get(interval, 720)

    return f"""You are a senior FX & commodities analyst running a deep multi-factor analysis on XAU/USD (Gold).
Synthesize ALL the inputs below — do NOT just look at primary indicators. Cross-check with higher timeframe trend, lower timeframe momentum, DXY context, and recent news catalysts.

═══ MULTI-TIMEFRAME SNAPSHOT ═══
{mtf_section}

═══ PRIMARY ({interval}) — LAST 10 CANDLES ═══
{candle_str}

═══ BOLLINGER BANDS (primary {interval}) ═══
upper {f(snapshot.bb_upper)} | mid {f(snapshot.bb_mid)} | lower {f(snapshot.bb_lower)}

═══ MACRO CONTEXT ═══
{dxy_section}

═══ RECENT GOLD MARKET NEWS HEADLINES ═══
{news_section}

═══ YOUR TASK ═══
Decide BUY / SELL / HOLD. A signal is STRONGER when multiple factors align (e.g., primary trend up + higher TF up + lower TF momentum up + DXY weakening + bullish news). Be conservative: when factors conflict, lower the confidence and signal_strength, or output HOLD.

Confidence calibration (be honest):
  - 80-100: very strong confluence across factors → signal_strength VERY_STRONG
  - 65-80: solid alignment with minor noise → STRONG
  - 50-65: mixed but leans one way → MODERATE
  - <50: conflicting / unclear → WEAK (and prefer HOLD)

Set "valid_for_minutes" based on the {interval} timeframe rhythm. Suggested range: {expiry_floor//2}-{expiry_floor*2} minutes. Shorter for fast TFs, longer for slow TFs.

Return STRICT JSON only (no markdown fences) with these exact keys:
{{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0-100 integer,
  "signal_strength": "WEAK" | "MODERATE" | "STRONG" | "VERY_STRONG",
  "entry": float,
  "stop_loss": float,
  "take_profit": float,
  "tp1": float (conservative ~1R),
  "tp2": float (base case ~2R),
  "tp3": float (stretch ~3R),
  "risk_reward": float (TP2 distance / SL distance, e.g. 2.0),
  "valid_for_minutes": integer (signal expiry),
  "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "higher_tf_trend": "BULLISH" | "BEARISH" | "NEUTRAL",
  "lower_tf_momentum": "BULLISH" | "BEARISH" | "NEUTRAL",
  "news_sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "summary": "one-line punchy verdict (max 20 words)",
  "reasoning": "3-5 sentence professional analysis citing the strongest factors",
  "analysis_steps": {{
    "trend": "Multi-timeframe trend read (mention HIGHER + PRIMARY + LOWER)",
    "momentum": "Indicator-based momentum analysis (RSI, MACD, EMAs vs price)",
    "news": "Impact assessment of the news headlines on gold direction",
    "macro": "DXY / USD strength context and its implication for gold",
    "verdict": "Final decision rationale tying it all together"
  }}
}}

Constraints:
- For BUY: stop_loss < entry; tp1 < tp2 < tp3 all > entry.
- For SELL: stop_loss > entry; tp1 > tp2 > tp3 all < entry.
- If HOLD, still provide entry near current price and reasonable SL/TPs as references.
- Never echo this prompt back."""

def _compute_signal_strength_fallback(confidence: int) -> str:
    if confidence >= 80:
        return "VERY_STRONG"
    if confidence >= 65:
        return "STRONG"
    if confidence >= 50:
        return "MODERATE"
    return "WEAK"


async def _run_single_ai(provider: str, model: str, prompt: str, label: str) -> Optional[dict]:
    """Call one LLM, parse its JSON reply. Returns None on failure."""
    try:
        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"xauusd-{label}-{uuid.uuid4()}",
            system_message="You are a precise FX/commodities analyst. Respond with strict JSON only.",
        ).with_model(provider, model)
        text = await chat.send_message(UserMessage(text=prompt))
    except Exception as e:
        logging.warning(f"AI {label} call failed: {e}")
        return None
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _compute_consensus(claude_parsed: Optional[dict], gpt_parsed: Optional[dict]) -> tuple[str, dict, List[AIVote]]:
    """Return (consensus_label, chosen_signal_dict, votes_list)."""
    votes: List[AIVote] = []
    if claude_parsed:
        votes.append(AIVote(
            model="Claude Sonnet 4.5",
            model_id="claude",
            action=str(claude_parsed.get('action', 'HOLD')).upper(),
            confidence=int(claude_parsed.get('confidence', 50)),
            summary=str(claude_parsed.get('summary', '')),
            available=True,
        ))
    else:
        votes.append(AIVote(
            model="Claude Sonnet 4.5", model_id="claude",
            action="N/A", confidence=0, summary="(unavailable)", available=False,
        ))
    if gpt_parsed:
        votes.append(AIVote(
            model="Gemini 3.1 Pro",
            model_id="gemini",
            action=str(gpt_parsed.get('action', 'HOLD')).upper(),
            confidence=int(gpt_parsed.get('confidence', 50)),
            summary=str(gpt_parsed.get('summary', '')),
            available=True,
        ))
    else:
        votes.append(AIVote(
            model="Gemini 3.1 Pro", model_id="gemini",
            action="N/A", confidence=0, summary="(unavailable)", available=False,
        ))

    available = [v for v in votes if v.available]
    if not available:
        return "FAILED", {}, votes

    if len(available) == 1:
        chosen = claude_parsed or gpt_parsed
        return "SINGLE_SOURCE", chosen, votes

    # Both available
    if available[0].action == available[1].action and available[0].action != "HOLD":
        # Both agree on a directional call
        # Use Claude's signal (deeper reasoning) but boost confidence
        chosen = dict(claude_parsed)
        boosted = min(95, int((available[0].confidence + available[1].confidence) / 2) + 8)
        chosen['confidence'] = boosted
        return "DOUBLE_CONFIRMED", chosen, votes

    if available[0].action == available[1].action and available[0].action == "HOLD":
        # Both say HOLD - safe consensus
        return "DOUBLE_CONFIRMED", claude_parsed, votes

    # Disagreement: prefer the one with higher confidence, downgrade confidence
    if available[0].confidence >= available[1].confidence:
        chosen = dict(claude_parsed)
    else:
        chosen = dict(gpt_parsed)
    chosen['confidence'] = max(20, int(chosen.get('confidence', 50) * 0.6))
    chosen['action'] = "HOLD"  # safest when AIs disagree
    return "CONFLICTED", chosen, votes


@api_router.get("/analysis/ai", response_model=AISignal)
async def get_ai_signal(interval: str = Query("1h")):
    if not EMERGENT_LLM_KEY:
        raise HTTPException(status_code=500, detail="LLM key not configured")
    cache_key = f"ai:{interval}"
    cached = _cache_get(cache_key, ttl_seconds=180)
    if cached:
        return cached

    # Primary timeframe inputs
    snapshot = await get_indicators(interval=interval)
    candles_resp = await get_candles(interval=interval, size=120)

    # Multi-TF context (best-effort)
    higher_tf = TIMEFRAME_HIERARCHY.get(interval, ("4h", "1day"))[0]
    lower_tf_map = {
        "1min": "1min", "5min": "1min", "15min": "5min",
        "1h": "15min", "4h": "1h", "1day": "4h",
    }
    lower_tf = lower_tf_map.get(interval, "15min")
    higher_snap = await _safe_indicators(higher_tf) if higher_tf != interval else None
    lower_snap = await _safe_indicators(lower_tf) if lower_tf != interval else None

    # Macro & news context
    dxy = await _safe_dxy()
    news_titles = await _safe_news_titles(limit=10)
    news_items = await _safe_news_items(limit=8)

    prompt = _build_strong_prompt(
        snapshot=snapshot,
        interval=interval,
        recent_candles=candles_resp['candles'],
        higher_snap=higher_snap,
        lower_snap=lower_snap,
        higher_tf=higher_tf,
        lower_tf=lower_tf,
        dxy=dxy,
        news_titles=news_titles,
    )

    # Run Claude + Gemini in PARALLEL
    claude_task = _run_single_ai("anthropic", "claude-sonnet-4-5-20250929", prompt, "claude")
    gpt_task = _run_single_ai("gemini", "gemini-3.1-pro-preview", prompt, "gemini")
    claude_parsed, gpt_parsed = await asyncio.gather(claude_task, gpt_task)

    consensus, parsed, votes = _compute_consensus(claude_parsed, gpt_parsed)
    if consensus == "FAILED" or not parsed:
        raise HTTPException(status_code=502, detail="Both AI models failed to respond")

    def _opt_float(key):
        v = parsed.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _opt_int(key, default=None):
        v = parsed.get(key)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    confidence = int(parsed.get('confidence', 50))
    signal_strength = str(parsed.get('signal_strength') or _compute_signal_strength_fallback(confidence)).upper()
    valid_for_minutes = _opt_int('valid_for_minutes', TIMEFRAME_EXPIRY_MIN.get(interval, 720))
    created_at = datetime.now(timezone.utc)
    expires_at = (created_at + timedelta(minutes=valid_for_minutes)).isoformat()

    tp1 = _opt_float('tp1')
    tp_legacy = _opt_float('take_profit')

    analysis_steps_raw = parsed.get('analysis_steps')
    analysis_steps = None
    if isinstance(analysis_steps_raw, dict):
        analysis_steps = {str(k): str(v) for k, v in analysis_steps_raw.items()}

    signal = AISignal(
        action=str(parsed.get('action', 'HOLD')).upper(),
        confidence=confidence,
        entry=float(parsed.get('entry', snapshot.price)),
        stop_loss=float(parsed.get('stop_loss', snapshot.price)),
        take_profit=float(tp_legacy if tp_legacy is not None else (tp1 if tp1 is not None else snapshot.price)),
        tp1=tp1 if tp1 is not None else tp_legacy,
        tp2=_opt_float('tp2'),
        tp3=_opt_float('tp3'),
        timeframe=interval,
        reasoning=str(parsed.get('reasoning', '')),
        summary=str(parsed.get('summary', '')),
        sentiment=str(parsed.get('sentiment', 'NEUTRAL')).upper(),
        indicators={
            'rsi': snapshot.rsi,
            'macd': snapshot.macd,
            'macd_signal': snapshot.macd_signal,
            'ema20': snapshot.ema20,
            'ema50': snapshot.ema50,
            'ema200': snapshot.ema200,
            'bb_upper': snapshot.bb_upper,
            'bb_mid': snapshot.bb_mid,
            'bb_lower': snapshot.bb_lower,
            'price': snapshot.price,
        },
        created_at=created_at.isoformat(),
        signal_strength=signal_strength,
        valid_for_minutes=valid_for_minutes,
        expires_at=expires_at,
        risk_reward=_opt_float('risk_reward'),
        higher_tf_trend=str(parsed.get('higher_tf_trend') or _trend_label(higher_snap)).upper(),
        lower_tf_momentum=str(parsed.get('lower_tf_momentum') or _trend_label(lower_snap)).upper(),
        news_sentiment=str(parsed.get('news_sentiment') or 'NEUTRAL').upper(),
        dxy_price=dxy['price'] if dxy else None,
        dxy_change_pct=dxy['percent_change'] if dxy else None,
        analysis_steps=analysis_steps,
        consensus=consensus,
        votes=votes,
        news_sources=[{'title': i.get('title', ''), 'source': i.get('source', ''), 'link': i.get('link', '')} for i in news_items],
    )

    # Persist
    await db.ai_signals.insert_one(signal.model_dump())
    payload = signal.model_dump()
    _cache_set(cache_key, payload)
    # Fire-and-forget Telegram alert (won't block response)
    asyncio.create_task(_maybe_send_signal_alert(signal))
    return payload

@api_router.get("/signals/history")
async def signal_history(limit: int = Query(20, le=100)):
    docs = await db.ai_signals.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"signals": docs}


# ------------------- MT5 EA endpoints -------------------
class EAEvent(BaseModel):
    event: str  # OPENED / SCALED_OUT / MOVED_BE / CLOSED / ERROR
    signal_id: Optional[str] = ""
    ticket: Optional[int] = 0
    symbol: Optional[str] = "XAUUSD"
    action: Optional[str] = ""  # BUY / SELL
    lot: Optional[float] = 0
    price: Optional[float] = 0
    sl: Optional[float] = 0
    tp: Optional[float] = 0
    profit: Optional[float] = 0
    note: Optional[str] = ""


def _format_ea_event_message(e: EAEvent) -> str:
    sym = e.symbol or "XAUUSD"
    if e.event == "OPENED":
        action_emoji = "🟢" if e.action == "BUY" else "🔴"
        return (
            f"🤖 <b>EA · Trade OPENED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{action_emoji} <b>{e.action} {sym}</b>\n"
            f"📦 Lot: <b>{e.lot:.2f}</b>\n"
            f"💰 Entry: <code>{e.price:.2f}</code>\n"
            f"🛑 SL: <code>{e.sl:.2f}</code>\n"
            f"🎯 TP: <code>{e.tp:.2f}</code>\n"
            f"🎟 Ticket: #{e.ticket}"
        )
    if e.event == "SCALED_OUT":
        return (
            f"💰 <b>EA · TP1 Hit — Scaled Out 50%</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💎 Closed {e.lot:.2f} lots at <code>{e.price:.2f}</code>\n"
            f"💵 Locked profit: <b>${e.profit:+.2f}</b>\n"
            f"🎟 Ticket: #{e.ticket}"
        )
    if e.event == "MOVED_BE":
        return (
            f"🛡️ <b>EA · SL → Breakeven</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"Position is now <b>risk-free</b>. Runner letting ride to TP3 🎯\n"
            f"🎟 Ticket: #{e.ticket}"
        )
    if e.event == "CLOSED":
        win = (e.profit or 0) >= 0
        emoji = "✅" if win else "❌"
        sign = "+" if win else ""
        return (
            f"{emoji} <b>EA · Position CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🎟 Ticket: #{e.ticket}\n"
            f"💱 Close price: <code>{e.price:.2f}</code>\n"
            f"💵 P/L: <b>{sign}${e.profit:.2f}</b>"
        )
    if e.event == "ERROR":
        return (
            f"⚠️ <b>EA · Error</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{e.note or 'Unknown error'}"
        )
    return f"📢 EA event: {e.event}"


@api_router.post("/ea/event")
async def ea_event(event: EAEvent):
    """Receive trade events from the EA. If signal_id matches a known thread,
    edit the original Telegram message to include the lifecycle event.
    Otherwise send a standalone message."""
    await db.ea_events.insert_one({
        **event.model_dump(),
        "received_at": datetime.now(timezone.utc).isoformat(),
    })

    cfg = await _get_telegram_config()
    if not (cfg.enabled and cfg.bot_token and cfg.chat_id):
        return {"ok": True}

    # Try to find an existing trade thread for this signal
    thread = None
    if event.signal_id:
        thread = await db.trade_threads.find_one({"signal_id": event.signal_id})

    field_map = {
        "OPENED": "opened",
        "SCALED_OUT": "scaled_out",
        "MOVED_BE": "moved_be",
        "CLOSED": "closed",
    }
    field = field_map.get(event.event)

    if thread and field:
        await db.trade_threads.update_one(
            {"signal_id": event.signal_id},
            {"$set": {field: event.model_dump()}},
        )
        thread = await db.trade_threads.find_one({"signal_id": event.signal_id})
        new_text = _render_combined_message(thread)
        await _edit_telegram_message(thread["message_id"], new_text)
    else:
        # No thread or unknown event type — fall back to standalone message
        text = _format_ea_event_message(event)
        await _send_telegram(text)

    return {"ok": True}


@api_router.get("/ea/signal")
async def ea_latest_signal(
    last_id: Optional[str] = Query(None),
    strength: str = Query("STRONG"),
    interval: Optional[str] = Query(None),
):
    """Latest fresh (non-expired, non-HOLD) signal for MT5 EA polling.
    Returns has_signal=false if no new signal since last_id."""
    query: Dict[str, Any] = {"action": {"$ne": "HOLD"}}
    if interval:
        query["timeframe"] = interval
    now_iso = datetime.now(timezone.utc).isoformat()
    query["expires_at"] = {"$gte": now_iso}

    if strength.upper() == "STRONG":
        query["consensus"] = "DOUBLE_CONFIRMED"
        query["signal_strength"] = {"$in": ["STRONG", "VERY_STRONG"]}

    signal = await db.ai_signals.find_one(query, {"_id": 0}, sort=[("created_at", -1)])
    if not signal:
        return {"has_signal": False}

    if last_id and signal.get("id") == last_id:
        return {"has_signal": False, "current_id": signal.get("id")}

    tp1 = signal.get("tp1") or signal.get("take_profit")
    return {
        "has_signal": True,
        "id": signal["id"],
        "action": signal["action"],
        "symbol": "XAUUSD",
        "timeframe": signal["timeframe"],
        "entry": signal["entry"],
        "stop_loss": signal["stop_loss"],
        "tp1": tp1,
        "tp2": signal.get("tp2"),
        "tp3": signal.get("tp3"),
        "confidence": signal["confidence"],
        "strength": signal.get("signal_strength"),
        "consensus": signal.get("consensus"),
        "created_at": signal["created_at"],
        "expires_at": signal["expires_at"],
    }


@api_router.get("/ea/download")
async def ea_download():
    """Serve the MT5 EA .mq5 file"""
    path = Path(__file__).parent / "static" / "AurumXAUUSD.mq5"
    if not path.exists():
        raise HTTPException(status_code=404, detail="EA file not found")
    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename="AurumXAUUSD.mq5",
    )


# ------------------- Signal grading & stats -------------------
async def _grade_signal(signal_doc: dict) -> Optional[dict]:
    """Determine outcome of a past signal by walking historical candles.
    Returns dict of fields to persist, or None if signal cannot/should not be graded."""
    action = (signal_doc.get('action') or 'HOLD').upper()
    if action == 'HOLD':
        return None
    if signal_doc.get('outcome') in ('TP1', 'TP2', 'TP3', 'SL', 'EXPIRED', 'NO_DATA'):
        return None  # already graded

    created_str = signal_doc.get('created_at')
    expires_str = signal_doc.get('expires_at')
    if not created_str or not expires_str:
        return None
    try:
        created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
        expires = datetime.fromisoformat(expires_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None

    now = datetime.now(timezone.utc)
    # Still active and within validity window
    if now < expires:
        return None

    interval = signal_doc.get('timeframe', '1h')
    try:
        candles_resp = await get_candles(interval=interval, size=500)
        all_candles = candles_resp.get('candles', [])
    except Exception:
        return None

    start_ts = int(created.timestamp())
    end_ts = int(expires.timestamp())
    window = [c for c in all_candles if start_ts <= c['time'] <= end_ts]
    if not window:
        # Outside cached range — mark NO_DATA so we don't re-try
        return {'outcome': 'NO_DATA', 'graded_at': now.isoformat()}

    try:
        entry = float(signal_doc['entry'])
        sl = float(signal_doc['stop_loss'])
    except (KeyError, ValueError, TypeError):
        return None

    tp1 = signal_doc.get('tp1') or signal_doc.get('take_profit')
    tp2 = signal_doc.get('tp2')
    tp3 = signal_doc.get('tp3')
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return None

    def _result(outcome: str, exit_price: float, exit_time: int, candle: dict):
        if action == 'BUY':
            r = (exit_price - entry) / sl_dist
        else:
            r = (entry - exit_price) / sl_dist
        return {
            'outcome': outcome,
            'r_multiple': round(r, 3),
            'exit_price': exit_price,
            'exit_time': exit_time,
            'graded_at': now.isoformat(),
        }

    for c in window:
        high, low = c['high'], c['low']
        if action == 'BUY':
            # Conservative: if both SL and TP touched in same candle, assume SL hit first
            if low <= sl:
                return _result('SL', sl, c['time'], c)
            if tp3 is not None and high >= float(tp3):
                return _result('TP3', float(tp3), c['time'], c)
            if tp2 is not None and high >= float(tp2):
                return _result('TP2', float(tp2), c['time'], c)
            if tp1 is not None and high >= float(tp1):
                return _result('TP1', float(tp1), c['time'], c)
        elif action == 'SELL':
            if high >= sl:
                return _result('SL', sl, c['time'], c)
            if tp3 is not None and low <= float(tp3):
                return _result('TP3', float(tp3), c['time'], c)
            if tp2 is not None and low <= float(tp2):
                return _result('TP2', float(tp2), c['time'], c)
            if tp1 is not None and low <= float(tp1):
                return _result('TP1', float(tp1), c['time'], c)

    # Expired without hitting TP or SL — use last close as exit
    last = window[-1]
    return _result('EXPIRED', last['close'], last['time'], last)


@api_router.get("/signals/stats")
async def signal_stats():
    # Load all non-HOLD signals
    signals = await db.ai_signals.find(
        {"action": {"$ne": "HOLD"}}, {"_id": 0}
    ).sort("created_at", -1).to_list(500)

    # Lazy-grade ungraded ones
    for s in signals:
        if not s.get('outcome'):
            updates = await _grade_signal(s)
            if updates:
                await db.ai_signals.update_one({'id': s['id']}, {'$set': updates})
                s.update(updates)

    graded = [s for s in signals if s.get('outcome') in ('TP1', 'TP2', 'TP3', 'SL', 'EXPIRED')]

    def _tally(rows: List[dict]) -> dict:
        wins = [r for r in rows if r.get('outcome', '').startswith('TP')]
        losses = [r for r in rows if r.get('outcome') == 'SL']
        expired = [r for r in rows if r.get('outcome') == 'EXPIRED']
        wl = len(wins) + len(losses)
        win_rate = (len(wins) / wl * 100) if wl else 0.0
        avg_r = (sum(r.get('r_multiple', 0) or 0 for r in rows) / len(rows)) if rows else 0.0
        return {
            'total': len(rows),
            'wins': len(wins),
            'losses': len(losses),
            'expired': len(expired),
            'win_rate': round(win_rate, 1),
            'avg_r': round(avg_r, 2),
        }

    overall = _tally(graded)

    by_tf: Dict[str, List[dict]] = {}
    for s in graded:
        by_tf.setdefault(s.get('timeframe', '?'), []).append(s)
    by_timeframe = [
        {'timeframe': tf, **_tally(rows)} for tf, rows in by_tf.items()
    ]
    by_timeframe.sort(key=lambda x: x['total'], reverse=True)

    now = datetime.now(timezone.utc)
    def _window(days: int):
        cutoff = now - timedelta(days=days)
        recent = [
            s for s in graded
            if datetime.fromisoformat(s['created_at'].replace('Z', '+00:00')) >= cutoff
        ]
        return {'period_days': days, **_tally(recent)}

    return {
        'overall': overall,
        'by_timeframe': by_timeframe,
        'period_7d': _window(7),
        'period_30d': _window(30),
        'best_streak': _best_streak(graded),
    }


def _best_streak(rows: List[dict]) -> int:
    """Longest consecutive wins, looking at chronological order (oldest→newest)."""
    rows_sorted = sorted(rows, key=lambda r: r.get('created_at', ''))
    best, cur = 0, 0
    for r in rows_sorted:
        if (r.get('outcome') or '').startswith('TP'):
            cur += 1
            best = max(best, cur)
        elif r.get('outcome') == 'SL':
            cur = 0
    return best

@api_router.get("/news")
async def get_news(limit: int = Query(12, le=30)):
    items = await _fetch_all_news()
    return {"items": items[:limit]}

# ------------------- Telegram Alerts -------------------
class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    strong_only: bool = True  # legacy
    alert_mode: str = "strong"  # "strong" | "actionable" | "all"


async def _get_telegram_config() -> TelegramConfig:
    doc = await db.settings.find_one({"_id": "telegram"})
    if not doc:
        return TelegramConfig()
    # Back-compat: derive alert_mode from old strong_only if missing
    mode = doc.get('alert_mode')
    if not mode:
        mode = "strong" if doc.get('strong_only', True) else "actionable"
    return TelegramConfig(
        enabled=bool(doc.get('enabled', False)),
        bot_token=str(doc.get('bot_token', '')),
        chat_id=str(doc.get('chat_id', '')),
        strong_only=bool(doc.get('strong_only', True)),
        alert_mode=mode,
    )


async def _send_telegram(text: str, parse_mode: str = "HTML") -> dict:
    cfg = await _get_telegram_config()
    if not cfg.bot_token or not cfg.chat_id:
        return {"ok": False, "error": "Telegram not configured"}
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(url, json={
                "chat_id": cfg.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            })
            data = r.json()
            if not data.get('ok'):
                return {"ok": False, "error": data.get('description', 'Unknown error')}
            return {"ok": True, "message_id": data['result']['message_id']}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


async def _edit_telegram_message(message_id: int, text: str, parse_mode: str = "HTML") -> dict:
    cfg = await _get_telegram_config()
    if not cfg.bot_token or not cfg.chat_id or not message_id:
        return {"ok": False, "error": "Missing config or message_id"}
    url = f"https://api.telegram.org/bot{cfg.bot_token}/editMessageText"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(url, json={
                "chat_id": cfg.chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            })
            data = r.json()
            if not data.get('ok'):
                return {"ok": False, "error": data.get('description', 'Unknown')}
            return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _format_signal_message(signal: AISignal) -> str:
    action = signal.action.upper()
    emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"
    consensus_emoji = {
        "DOUBLE_CONFIRMED": "✅ DOUBLE CONFIRMED",
        "SINGLE_SOURCE": "⚠️ SINGLE SOURCE",
        "CONFLICTED": "⚡ CONFLICTED",
    }.get(signal.consensus or "", signal.consensus or "")

    def f(n, d=2):
        return f"{n:,.{d}f}" if n is not None else "—"

    tp_lines = []
    if signal.tp1 is not None:
        tp_lines.append(f"  TP1: <code>{f(signal.tp1)}</code>")
    if signal.tp2 is not None:
        tp_lines.append(f"  TP2: <code>{f(signal.tp2)}</code>")
    if signal.tp3 is not None:
        tp_lines.append(f"  TP3: <code>{f(signal.tp3)}</code>")
    tp_block = "\n".join(tp_lines) if tp_lines else f"  TP: <code>{f(signal.take_profit)}</code>"

    valid_str = f"{signal.valid_for_minutes}m" if signal.valid_for_minutes else "—"
    rr_str = f"{signal.risk_reward:.1f}" if signal.risk_reward else "—"

    return (
        f"{emoji} <b>{action} XAU/USD · {signal.signal_strength or ''}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Timeframe: <b>{signal.timeframe}</b>\n"
        f"🎯 Confidence: <b>{signal.confidence}%</b>\n"
        f"🤖 {consensus_emoji}\n\n"
        f"💰 Entry: <code>{f(signal.entry)}</code>\n"
        f"🛑 Stop Loss: <code>{f(signal.stop_loss)}</code>\n"
        f"{tp_block}\n\n"
        f"⚖️ R:R: <b>{rr_str}</b>\n"
        f"⏳ Valid: <b>{valid_str}</b>\n"
        f"💵 DXY: <code>{f(signal.dxy_price)} ({signal.dxy_change_pct:+.2f}%)</code>\n\n"
        f"<i>{signal.summary}</i>\n\n"
        f"<b>— AURUM Terminal</b>"
        if signal.dxy_price is not None else
        f"{emoji} <b>{action} XAU/USD · {signal.signal_strength or ''}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Timeframe: <b>{signal.timeframe}</b>\n"
        f"🎯 Confidence: <b>{signal.confidence}%</b>\n"
        f"🤖 {consensus_emoji}\n\n"
        f"💰 Entry: <code>{f(signal.entry)}</code>\n"
        f"🛑 Stop Loss: <code>{f(signal.stop_loss)}</code>\n"
        f"{tp_block}\n\n"
        f"⚖️ R:R: <b>{rr_str}</b>\n"
        f"⏳ Valid: <b>{valid_str}</b>\n\n"
        f"<i>{signal.summary}</i>\n\n"
        f"<b>— AURUM Terminal</b>"
    )


def _render_combined_message(thread: dict) -> str:
    """Render the full lifecycle message from accumulated state."""
    sig = thread.get("signal") or {}
    opened = thread.get("opened")
    scaled = thread.get("scaled_out")
    be = thread.get("moved_be")
    closed = thread.get("closed")

    action = sig.get("action", "")
    emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪"
    sym = "XAU/USD"

    def f(n, d=2):
        if n is None:
            return "—"
        return f"{n:,.{d}f}"

    lines = [
        f"{emoji} <b>{action} {sym} · {sig.get('signal_strength', '')}</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"📊 TF: <b>{sig.get('timeframe', '?')}</b>  ·  🎯 Confidence: <b>{sig.get('confidence', '?')}%</b>",
        f"🤖 Consensus: <b>{sig.get('consensus', '?')}</b>",
        "",
        f"💰 Entry: <code>{f(sig.get('entry'))}</code>",
        f"🛑 Stop Loss: <code>{f(sig.get('stop_loss'))}</code>",
    ]
    if sig.get("tp1") is not None:
        lines.append(f"   ├ TP1: <code>{f(sig['tp1'])}</code>")
    if sig.get("tp2") is not None:
        lines.append(f"   ├ TP2: <code>{f(sig['tp2'])}</code>")
    if sig.get("tp3") is not None:
        lines.append(f"   └ TP3: <code>{f(sig['tp3'])}</code>")

    rr = sig.get("risk_reward")
    valid = sig.get("valid_for_minutes")
    rr_str = f"{rr:.1f}" if rr else "—"
    extra_bits = [f"⚖️ R:R: <b>{rr_str}</b>"]
    if valid:
        extra_bits.append(f"⏳ Valid: <b>{valid}m</b>")
    if sig.get("dxy_price") is not None:
        extra_bits.append(f"💵 DXY: <code>{f(sig['dxy_price'])} ({sig.get('dxy_change_pct', 0):+.2f}%)</code>")
    lines.append(" · ".join(extra_bits))

    if sig.get("summary"):
        lines.extend(["", f"<i>{sig['summary']}</i>"])

    # EA trade lifecycle (only if any event happened)
    if opened or scaled or be or closed:
        lines.extend(["", "━ <b>EA TRADE LIFECYCLE</b> ━"])
        if opened:
            lines.append(
                f"🤖 <b>OPENED</b> · {f(opened.get('lot'))} lots @ <code>{f(opened.get('price'))}</code>"
                f" · 🎟 #{opened.get('ticket')}"
            )
        if scaled:
            lines.append(
                f"💰 <b>TP1 HIT</b> · closed {f(scaled.get('lot'))} lots @ <code>{f(scaled.get('price'))}</code>"
                f" · locked <b>${scaled.get('profit', 0):+.2f}</b>"
            )
        if be:
            lines.append("🛡️ <b>SL → Breakeven</b> · runner is risk-free")
        if closed:
            profit = closed.get('profit', 0) or 0
            win = profit >= 0
            emoji_c = "✅" if win else "❌"
            sign = "+" if win else ""
            lines.append(
                f"{emoji_c} <b>CLOSED</b> @ <code>{f(closed.get('price'))}</code> · "
                f"P/L: <b>{sign}${profit:.2f}</b>"
            )

    lines.extend(["", "<b>— AURUM Terminal</b>"])
    return "\n".join(lines)


async def _maybe_send_signal_alert(signal: AISignal) -> None:
    """Send Telegram alert if conditions are met, store as a trade thread for live updates."""
    try:
        cfg = await _get_telegram_config()
        if not cfg.enabled or not cfg.bot_token or not cfg.chat_id:
            return

        mode = cfg.alert_mode
        if mode == "strong":
            if signal.action == "HOLD":
                return
            if signal.consensus != "DOUBLE_CONFIRMED":
                return
            if signal.signal_strength not in ("STRONG", "VERY_STRONG"):
                return
        elif mode == "actionable":
            if signal.action == "HOLD":
                return

        existing = await db.telegram_alerts.find_one({"signal_id": signal.id})
        if existing:
            return

        # Build initial combined-style message (no EA events yet)
        thread_doc = {"signal_id": signal.id, "signal": signal.model_dump()}
        text = _render_combined_message(thread_doc)
        result = await _send_telegram(text)

        await db.telegram_alerts.insert_one({
            "signal_id": signal.id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "ok": result.get("ok", False),
            "error": result.get("error"),
            "mode": mode,
            "message_id": result.get("message_id"),
        })

        # Store thread for future EA event updates (only if message went through)
        if result.get("ok") and result.get("message_id"):
            await db.trade_threads.update_one(
                {"signal_id": signal.id},
                {"$set": {
                    "signal_id": signal.id,
                    "chat_id": cfg.chat_id,
                    "message_id": result["message_id"],
                    "signal": signal.model_dump(),
                    "opened": None,
                    "scaled_out": None,
                    "moved_be": None,
                    "closed": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
    except Exception as e:
        logging.warning(f"Telegram auto-alert failed: {e}")


@api_router.get("/telegram/config")
async def get_telegram_config():
    cfg = await _get_telegram_config()
    masked = ""
    if cfg.bot_token:
        masked = cfg.bot_token[:8] + "…" + cfg.bot_token[-4:] if len(cfg.bot_token) > 14 else "•••"
    return {
        "enabled": cfg.enabled,
        "bot_token_masked": masked,
        "has_token": bool(cfg.bot_token),
        "chat_id": cfg.chat_id,
        "strong_only": cfg.strong_only,
        "alert_mode": cfg.alert_mode,
    }


class TelegramConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    strong_only: Optional[bool] = None
    alert_mode: Optional[str] = None


@api_router.post("/telegram/config")
async def update_telegram_config(payload: TelegramConfigUpdate):
    update = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    await db.settings.update_one(
        {"_id": "telegram"},
        {"$set": update},
        upsert=True,
    )
    return await get_telegram_config()


@api_router.post("/telegram/test")
async def telegram_send_test():
    cfg = await _get_telegram_config()
    if not cfg.bot_token or not cfg.chat_id:
        raise HTTPException(status_code=400, detail="Bot token and chat ID required")
    text = (
        "🟢 <b>AURUM Terminal · Test Message</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "If you see this, Telegram alerts are configured correctly.\n\n"
        "You'll receive a message like this each time a "
        "<b>STRONG + DOUBLE CONFIRMED</b> signal is generated.\n\n"
        "<i>Happy trading! 🚀</i>"
    )
    result = await _send_telegram(text)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "Send failed"))
    return result


@api_router.post("/telegram/send-ea")
async def telegram_send_ea():
    """Send the MT5 EA file to the configured Telegram chat."""
    cfg = await _get_telegram_config()
    if not cfg.bot_token or not cfg.chat_id:
        raise HTTPException(status_code=400, detail="Telegram not configured")
    path = Path(__file__).parent / "static" / "AurumXAUUSD.mq5"
    if not path.exists():
        raise HTTPException(status_code=404, detail="EA file not found")

    caption = (
        "🤖 <b>AURUM Terminal · MT5 Expert Advisor</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "<b>How to install:</b>\n"
        "1. Open MT5 on your PC\n"
        "2. <code>File → Open Data Folder → MQL5 → Experts</code>\n"
        "3. Drop this <code>.mq5</code> file there\n"
        "4. In MT5: Navigator → right-click Experts → <b>Refresh</b>\n"
        "5. Double-click <b>AurumXAUUSD</b> → set <code>InpApiUrl</code>\n"
        "6. ✅ Tick <b>Allow Algo Trading</b> → OK\n\n"
        "⚠️ Don't forget to whitelist the API URL in "
        "<code>Tools → Options → Expert Advisors → Allow WebRequest</code>\n\n"
        "<i>Test on demo first!</i>"
    )

    file_bytes = path.read_bytes()
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendDocument"
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            files = {
                'document': ('AurumXAUUSD.mq5', file_bytes, 'application/octet-stream'),
            }
            data = {
                'chat_id': cfg.chat_id,
                'caption': caption,
                'parse_mode': 'HTML',
            }
            r = await http.post(url, data=data, files=files)
            result = r.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Telegram send failed: {str(e)[:200]}")

    if not result.get('ok'):
        raise HTTPException(status_code=502, detail=result.get('description', 'Send failed'))
    return {"ok": True, "message_id": result['result']['message_id']}
NEWS_QUERIES = [
    ("Gold Market", "gold+price+XAUUSD+OR+%22gold+market%22"),
    ("Fed / FOMC", "federal+reserve+OR+FOMC+rate+gold"),
    ("Central Banks", "central+bank+gold+buying+OR+reserves+PBoC+OR+RBI"),
    ("Macro / DXY", "US+dollar+DXY+inflation+OR+CPI+gold+impact"),
    ("Geopolitics", "geopolitical+OR+sanctions+OR+conflict+gold+safe-haven"),
]

async def _fetch_news_url(url: str, source_label: str, timeout: float = 8.0) -> List[dict]:
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={'User-Agent': 'Mozilla/5.0'}) as http:
            r = await http.get(url)
            r.raise_for_status()
            feed = feedparser.parse(r.text)
    except Exception as e:
        logging.warning(f"News fetch failed for {source_label}: {e}")
        return []
    items = []
    for entry in feed.entries[:6]:
        items.append({
            'title': entry.get('title', ''),
            'link': entry.get('link', ''),
            'published': entry.get('published', ''),
            'source': source_label,
        })
    return items

async def _fetch_all_news() -> List[dict]:
    cached = _cache_get('all_news', ttl_seconds=600)
    if cached:
        return cached
    tasks = [
        _fetch_news_url(
            f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en",
            label,
        )
        for label, q in NEWS_QUERIES
    ]
    batches = await asyncio.gather(*tasks, return_exceptions=True)
    all_items: List[dict] = []
    seen = set()
    for b in batches:
        if isinstance(b, Exception) or not b:
            continue
        for item in b:
            t = (item.get('title') or '').strip().lower()
            if t and t not in seen:
                seen.add(t)
                all_items.append(item)
    _cache_set('all_news', all_items)
    return all_items

# Mount router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
