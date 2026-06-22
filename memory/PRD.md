# PRD — AURUM TERMINAL (XAUUSD Trading Analytics)

## Original Problem Statement
> how can you help me build trading XAUUSD buy or sell type web but like tradingview but strong Analye ??

## User Choices
- Data source: TwelveData (real-time XAU/USD)
- Analysis: AI summary + technical indicators (both)
- Features: Live candlestick chart with timeframes, AI Buy/Sell signal w/ confidence, Technical indicators, News feed
- Auth: Public dashboard (no login)
- Visual: Dark professional trader theme
- AI: Claude Sonnet 4.5 via Emergent Universal LLM Key

## Architecture
- **Frontend**: React + TailwindCSS + lightweight-charts + shadcn (sonner) on port 3000
- **Backend**: FastAPI on port 8001 with `/api` prefix
- **DB**: MongoDB for AI signal history
- **External**: TwelveData REST API (quote, time_series) + Claude Sonnet 4.5 (LLM) + Google News RSS

## Endpoints
- `GET /api/market/quote` — live XAU/USD quote
- `GET /api/market/candles?interval=1h&size=200` — OHLCV candles
- `GET /api/analysis/indicators?interval=1h` — RSI, MACD, EMA(20/50/200), Bollinger
- `GET /api/analysis/ai?interval=1h` — AI buy/sell signal + reasoning
- `GET /api/signals/history` — past AI signals
- `GET /api/news` — gold market news (Google News RSS)

## Implemented (Feb 2026 — Phase 1)
- ✅ Real-time XAU/USD ticker bar with tick flash animations
- ✅ Candlestick chart (lightweight-charts) with 6 timeframes (1m, 5m, 15m, 1h, 4h, 1d)
- ✅ AI signal card with action (BUY/SELL/HOLD), confidence %, entry/SL/TP, verdict, reasoning
- ✅ Technical indicators panel (RSI gauge, MACD, EMAs vs price, Bollinger Bands)
- ✅ Signal history table (persisted in MongoDB)
- ✅ Gold market news feed (Google News RSS)
- ✅ Institutional dark theme (Chivo + JetBrains Mono fonts, gold accent)
- ✅ Auto-refresh (quote 20s, chart 30s, AI cached 120s)

## Backlog (P1)
- Multi-asset support (silver, oil, BTCUSD)
- Trade journal: log simulated trades & P/L
- Custom alerts: price/RSI/MACD triggers via email/Telegram
- Backtesting framework on AI signals

## Backlog (P2)
- WebSocket live tick stream (replace polling)
- Drawing tools on chart (trendlines, fibs)
- Account/portfolio sync (broker APIs)
- Mobile responsive optimization pass
