import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({ baseURL: API, timeout: 30000 });

export const fetchQuote = () => api.get("/market/quote").then(r => r.data);
export const fetchCandles = (interval, size = 150) =>
    api.get("/market/candles", { params: { interval, size } }).then(r => r.data);
export const fetchIndicators = (interval) =>
    api.get("/analysis/indicators", { params: { interval } }).then(r => r.data);
export const fetchAISignal = (interval, engine = "rule") =>
    api.get("/analysis/ai", { params: { interval, engine } }).then(r => r.data);
export const fetchSignalHistory = (limit = 10) =>
    api.get("/signals/history", { params: { limit } }).then(r => r.data);
export const fetchNews = (limit = 12) =>
    api.get("/news", { params: { limit } }).then(r => r.data);
export const fetchTelegramConfig = () => api.get("/telegram/config").then(r => r.data);
export const updateTelegramConfig = (payload) => api.post("/telegram/config", payload).then(r => r.data);
export const sendTelegramTest = () => api.post("/telegram/test").then(r => r.data);
export const sendEAToTelegram = () => api.post("/telegram/send-ea").then(r => r.data);
