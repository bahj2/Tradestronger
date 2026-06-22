from fastapi import FastAPI, APIRouter, HTTPException, Query
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
    return payload

@api_router.get("/signals/history")
async def signal_history(limit: int = Query(20, le=100)):
    docs = await db.ai_signals.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"signals": docs}

@api_router.get("/news")
async def get_news(limit: int = Query(12, le=30)):
    items = await _fetch_all_news()
    return {"items": items[:limit]}

# ------------------- News aggregation (multi-source) -------------------
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
