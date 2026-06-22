import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries, HistogramSeries } from "lightweight-charts";
import { fetchCandles } from "@/lib/api";
import { Loader2 } from "lucide-react";

export default function PriceChart({ interval, refreshKey }) {
    const containerRef = useRef(null);
    const chartRef = useRef(null);
    const seriesRef = useRef(null);
    const volRef = useRef(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    // Create chart once
    useEffect(() => {
        if (!containerRef.current) return;
        const chart = createChart(containerRef.current, {
            layout: {
                background: { color: "#121212" },
                textColor: "#A3A3A3",
                fontFamily: "'JetBrains Mono', monospace",
            },
            localization: { locale: "en-US" },
            grid: {
                vertLines: { color: "#1A1A1A" },
                horzLines: { color: "#1A1A1A" },
            },
            crosshair: { mode: 1 },
            rightPriceScale: { borderColor: "#262626" },
            timeScale: { borderColor: "#262626", timeVisible: true, secondsVisible: false },
            width: containerRef.current.clientWidth,
            height: 480,
        });
        const candleSeries = chart.addSeries(CandlestickSeries, {
            upColor: "#00B86B",
            downColor: "#FF2A55",
            borderUpColor: "#00B86B",
            borderDownColor: "#FF2A55",
            wickUpColor: "#00B86B",
            wickDownColor: "#FF2A55",
        });
        const volSeries = chart.addSeries(HistogramSeries, {
            priceFormat: { type: "volume" },
            priceScaleId: "",
            color: "rgba(212,175,55,0.4)",
        });
        volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

        chartRef.current = chart;
        seriesRef.current = candleSeries;
        volRef.current = volSeries;

        const onResize = () => {
            if (containerRef.current) {
                chart.applyOptions({ width: containerRef.current.clientWidth });
            }
        };
        window.addEventListener("resize", onResize);
        return () => {
            window.removeEventListener("resize", onResize);
            chart.remove();
            chartRef.current = null;
        };
    }, []);

    // Load candles
    useEffect(() => {
        let alive = true;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const data = await fetchCandles(interval, 200);
                if (!alive || !seriesRef.current) return;
                const cs = data.candles.map((c) => ({
                    time: c.time,
                    open: c.open,
                    high: c.high,
                    low: c.low,
                    close: c.close,
                }));
                const vols = data.candles.map((c) => ({
                    time: c.time,
                    value: c.volume || 0,
                    color: c.close >= c.open ? "rgba(0,184,107,0.35)" : "rgba(255,42,85,0.35)",
                }));
                seriesRef.current.setData(cs);
                volRef.current.setData(vols);
                chartRef.current?.timeScale().fitContent();
            } catch (e) {
                setError(e?.response?.data?.detail || e.message || "Failed to load chart");
            } finally {
                if (alive) setLoading(false);
            }
        };
        load();
        const id = window.setInterval(load, 30000);
        return () => { alive = false; window.clearInterval(id); };
    }, [interval, refreshKey]);

    return (
        <div className="bg-[#121212] border border-[#262626] rounded-sm" data-testid="price-chart">
            <div className="flex items-center justify-between px-4 py-2 border-b border-[#262626]">
                <div className="flex items-center gap-3">
                    <span className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em]">Chart</span>
                    <span className="font-mono font-bold text-sm text-white">XAU/USD · {interval}</span>
                </div>
                <div className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em]">
                    {loading ? <Loader2 className="w-3 h-3 inline animate-spin" /> : "Candlestick"}
                </div>
            </div>
            {error && (
                <div className="px-4 py-2 text-xs font-mono text-[#FF2A55] border-b border-[#262626]" data-testid="chart-error">
                    {error}
                </div>
            )}
            <div ref={containerRef} className="w-full" data-testid="chart-canvas" />
        </div>
    );
}
