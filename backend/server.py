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

def _cache_set(key: str, data: Any) -> None:
    _cache[key] = {'ts': datetime.now(timezone.utc), 'data': data}

TD_BASE = "https://api.twelvedata.com"
SYMBOL = "XAU/USD"

# ------------------- TwelveData helpers -------------------
async def td_get(path: str, params: dict) -> dict:
    params = {**params, 'apikey': TWELVEDATA_API_KEY}
    async with httpx.AsyncClient(timeout=15.0) as http:
        r = await http.get(f"{TD_BASE}/{path}", params=params)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get('status') == 'error':
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

class AISignal(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: str  # BUY / SELL / HOLD
    confidence: int  # 0-100
    entry: float
    stop_loss: float
    take_profit: float
    timeframe: str
    reasoning: str
    summary: str
    sentiment: str  # BULLISH / BEARISH / NEUTRAL
    indicators: Dict[str, Optional[float]]
    created_at: str

# ------------------- Routes -------------------
@api_router.get("/")
async def root():
    return {"service": "XAUUSD Trading Analytics API", "status": "ok"}

@api_router.get("/market/quote", response_model=Quote)
async def get_quote():
    cache = _cache_get('quote', ttl_seconds=15)
    if cache:
        return cache
    data = await td_get("quote", {"symbol": SYMBOL})
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
    data = await td_get("time_series", {"symbol": SYMBOL, "interval": interval, "outputsize": size})
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

def _build_ai_prompt(snapshot: IndicatorSnapshot, interval: str, recent_candles: List[dict]) -> str:
    last10 = recent_candles[-10:]
    candle_str = "\n".join(
        f"  {c['open']:.2f} O / {c['high']:.2f} H / {c['low']:.2f} L / {c['close']:.2f} C"
        for c in last10
    )
    def f(v):
        return f"{v:.4f}" if isinstance(v, (int, float)) else "n/a"
    return f"""You are a senior FX & commodities analyst. Analyze XAU/USD (Gold) on the {interval} timeframe and produce a trade signal.

Current price: {snapshot.price:.2f}

Technical snapshot:
- RSI(14): {f(snapshot.rsi)}
- MACD: {f(snapshot.macd)} | Signal: {f(snapshot.macd_signal)} | Hist: {f(snapshot.macd_hist)}
- EMA20: {f(snapshot.ema20)} | EMA50: {f(snapshot.ema50)} | EMA200: {f(snapshot.ema200)}
- Bollinger: upper {f(snapshot.bb_upper)}, mid {f(snapshot.bb_mid)}, lower {f(snapshot.bb_lower)}

Last 10 candles (oldest→newest):
{candle_str}

Return STRICT JSON only (no markdown, no prose around it), with these exact keys:
{{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0-100 integer,
  "entry": float (suggested entry near current price),
  "stop_loss": float,
  "take_profit": float,
  "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "summary": "one-line punchy verdict, max 18 words",
  "reasoning": "3-5 sentence professional analysis citing the indicators and price action"
}}

Be decisive. Use realistic SL/TP based on recent volatility (~1-2x ATR equivalent from candle ranges). Never echo this prompt back."""

@api_router.get("/analysis/ai", response_model=AISignal)
async def get_ai_signal(interval: str = Query("1h")):
    if not EMERGENT_LLM_KEY:
        raise HTTPException(status_code=500, detail="LLM key not configured")
    cache_key = f"ai:{interval}"
    cached = _cache_get(cache_key, ttl_seconds=120)
    if cached:
        return cached

    snapshot = await get_indicators(interval=interval)
    candles_resp = await get_candles(interval=interval, size=120)

    prompt = _build_ai_prompt(snapshot, interval, candles_resp['candles'])
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"xauusd-{uuid.uuid4()}",
        system_message="You are a precise, professional FX/commodities analyst. Always respond with strict JSON.",
    ).with_model("anthropic", "claude-sonnet-4-5-20250929")

    try:
        response_text = await chat.send_message(UserMessage(text=prompt))
    except Exception as e:
        logging.exception("LLM call failed")
        raise HTTPException(status_code=502, detail=f"AI analysis failed: {str(e)[:200]}")

    # Extract JSON
    text = response_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1:
        raise HTTPException(status_code=502, detail="AI did not return JSON")
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"AI JSON parse error: {e}")

    signal = AISignal(
        action=str(parsed.get('action', 'HOLD')).upper(),
        confidence=int(parsed.get('confidence', 50)),
        entry=float(parsed.get('entry', snapshot.price)),
        stop_loss=float(parsed.get('stop_loss', snapshot.price)),
        take_profit=float(parsed.get('take_profit', snapshot.price)),
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
        created_at=datetime.now(timezone.utc).isoformat(),
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
    cached = _cache_get('news', ttl_seconds=600)
    if cached:
        return cached
    url = "https://news.google.com/rss/search?q=gold+price+XAUUSD+OR+%22gold+market%22&hl=en-US&gl=US&ceid=US:en"
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={'User-Agent': 'Mozilla/5.0'}) as http:
            r = await http.get(url)
            r.raise_for_status()
            feed = feedparser.parse(r.text)
    except Exception as e:
        logging.warning(f"News fetch failed: {e}")
        return {"items": []}
    items = []
    for entry in feed.entries[:limit]:
        items.append({
            'title': entry.get('title', ''),
            'link': entry.get('link', ''),
            'published': entry.get('published', ''),
            'source': entry.get('source', {}).get('title', '') if hasattr(entry, 'source') else '',
        })
    result = {"items": items}
    _cache_set('news', result)
    return result

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
