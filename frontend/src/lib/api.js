import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({ baseURL: API, timeout: 30000 });

export const fetchQuote = () => api.get("/market/quote").then(r => r.data);
export const fetchCandles = (interval, size = 150) =>
    api.get("/market/candles", { params: { interval, size } }).then(r => r.data);
export const fetchIndicators = (interval) =>
    api.get("/analysis/indicators", { params: { interval } }).then(r => r.data);
export const fetchAISignal = (interval, engine = "rule", autoUpgrade = false, newsBlackout = false, blackoutWindow = 15, withRiskTeam = false) =>
    api.get("/analysis/ai", {
        params: {
            interval,
            engine,
            auto_upgrade: autoUpgrade,
            news_blackout: newsBlackout,
            blackout_window: blackoutWindow,
            with_risk_team: withRiskTeam,
        },
        // Council+Risk = up to 7 LLM calls (~60-90s); Council alone ~30-50s; Dual-AI ~30-60s; rule <2s
        timeout: engine === "council" ? (withRiskTeam ? 150000 : 100000) : 75000,
    }).then(r => r.data);
export const fetchCalendarEvents = (params = {}) =>
    api.get("/calendar/events", { params }).then(r => r.data);
export const fetchBlackoutStatus = (windowMinutes = 15) =>
    api.get("/calendar/blackout", { params: { window_minutes: windowMinutes } }).then(r => r.data);
export const fetchSignalHistory = (limit = 10) =>
    api.get("/signals/history", { params: { limit } }).then(r => r.data);
export const fetchNews = (limit = 12) =>
    api.get("/news", { params: { limit } }).then(r => r.data);
export const fetchTelegramConfig = () => api.get("/telegram/config").then(r => r.data);
export const updateTelegramConfig = (payload) => api.post("/telegram/config", payload).then(r => r.data);
export const sendTelegramTest = () => api.post("/telegram/test").then(r => r.data);
export const sendEAToTelegram = () => api.post("/telegram/send-ea").then(r => r.data);
