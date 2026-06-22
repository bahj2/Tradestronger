import { useEffect, useState, useCallback } from "react";
import TickerBar from "@/components/TickerBar";
import PriceChart from "@/components/PriceChart";
import AISignalCard from "@/components/AISignalCard";
import IndicatorsPanel from "@/components/IndicatorsPanel";
import NewsFeed from "@/components/NewsFeed";
import SignalHistory from "@/components/SignalHistory";
import WinRateTracker from "@/components/WinRateTracker";
import { Activity } from "lucide-react";
import { fetchAISignal } from "@/lib/api";

const TIMEFRAMES = [
    { id: "1min", label: "1m" },
    { id: "5min", label: "5m" },
    { id: "15min", label: "15m" },
    { id: "1h", label: "1H" },
    { id: "4h", label: "4H" },
    { id: "1day", label: "1D" },
];

export default function Dashboard() {
    const [interval, setInterval] = useState("15min");
    const [refreshKey, setRefreshKey] = useState(0);
    const [aiSignal, setAiSignal] = useState(null);
    const [aiLoading, setAiLoading] = useState(false);
    const [aiError, setAiError] = useState(null);
    const [confidenceThreshold, setConfidenceThreshold] = useState(60);

    const refresh = () => setRefreshKey((k) => k + 1);

    const loadAI = useCallback(async () => {
        setAiLoading(true);
        setAiError(null);
        try {
            const s = await fetchAISignal(interval);
            setAiSignal(s);
        } catch (e) {
            setAiError(e?.response?.data?.detail || e.message || "AI analysis failed");
            setAiSignal(null);
        } finally {
            setAiLoading(false);
        }
    }, [interval]);

    useEffect(() => {
        document.title = "AURUM TERMINAL — XAU/USD Pro Analytics";
    }, []);

    useEffect(() => { loadAI(); }, [loadAI, refreshKey]);

    return (
        <div className="min-h-screen bg-[#0A0A0A] text-white">
            {/* Header */}
            <header className="border-b border-[#262626] bg-[#0A0A0A] sticky top-0 z-50">
                <div className="flex items-center justify-between px-4 py-3">
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 bg-[#D4AF37] flex items-center justify-center rounded-sm">
                            <span className="text-black font-black font-mono text-sm">AU</span>
                        </div>
                        <div>
                            <h1 className="font-black tracking-tighter text-lg leading-none" data-testid="app-title">
                                AURUM<span className="text-[#D4AF37]">.</span>TERMINAL
                            </h1>
                            <p className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em]">
                                XAU/USD · Institutional Analytics
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-3">
                        <div className="hidden md:flex items-center gap-2 text-xs font-mono text-gray-400">
                            <span className="w-1.5 h-1.5 rounded-full bg-[#00B86B] live-dot" />
                            <span>LIVE · TWELVEDATA</span>
                        </div>
                        <button
                            onClick={refresh}
                            data-testid="refresh-btn"
                            className="text-xs font-mono uppercase tracking-wider px-3 py-1.5 border border-[#262626] hover:border-[#D4AF37] hover:text-[#D4AF37] transition-colors rounded-sm"
                        >
                            <Activity className="inline w-3 h-3 mr-1" /> Refresh
                        </button>
                    </div>
                </div>
                <TickerBar refreshKey={refreshKey} />
            </header>

            {/* Timeframe selector */}
            <div className="border-b border-[#262626] bg-[#0A0A0A] px-4 py-2 flex items-center gap-1 overflow-x-auto">
                <span className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em] mr-3">
                    Timeframe
                </span>
                {TIMEFRAMES.map((tf) => (
                    <button
                        key={tf.id}
                        data-testid={`timeframe-${tf.id}-btn`}
                        onClick={() => setInterval(tf.id)}
                        className={`text-xs font-mono px-3 py-1 rounded-sm transition-all ${
                            interval === tf.id
                                ? "bg-[#262626] text-[#D4AF37]"
                                : "text-gray-400 hover:text-white"
                        }`}
                    >
                        {tf.label}
                    </button>
                ))}
                <div className="ml-auto flex items-center gap-3">
                    <div className="hidden md:flex items-center gap-2">
                        <label className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em]" htmlFor="conf-thresh">
                            Min Confidence
                        </label>
                        <input
                            id="conf-thresh"
                            data-testid="confidence-threshold-slider"
                            type="range"
                            min={0}
                            max={95}
                            step={5}
                            value={confidenceThreshold}
                            onChange={(e) => setConfidenceThreshold(parseInt(e.target.value, 10))}
                            className="w-28 accent-[#D4AF37]"
                        />
                        <span className="text-xs font-mono text-[#D4AF37] w-10" data-testid="confidence-threshold-value">
                            {confidenceThreshold}%
                        </span>
                    </div>
                    <div className="text-[10px] font-mono text-gray-600 uppercase tracking-[0.2em]">
                        Selected · <span className="text-[#D4AF37]">{interval}</span>
                    </div>
                </div>
            </div>

            {/* Main grid */}
            <main className="p-2 grid grid-cols-12 gap-2">
                <section className="col-span-12 xl:col-span-9">
                    <PriceChart
                        interval={interval}
                        refreshKey={refreshKey}
                        aiSignal={aiSignal && aiSignal.confidence >= confidenceThreshold ? aiSignal : null}
                    />
                    <div className="mt-2">
                        <WinRateTracker refreshKey={refreshKey} />
                    </div>
                    <div className="mt-2 grid grid-cols-12 gap-2">
                        <div className="col-span-12 lg:col-span-7">
                            <IndicatorsPanel interval={interval} refreshKey={refreshKey} />
                        </div>
                        <div className="col-span-12 lg:col-span-5">
                            <SignalHistory refreshKey={refreshKey} />
                        </div>
                    </div>
                </section>
                <aside className="col-span-12 xl:col-span-3 space-y-2">
                    <AISignalCard
                        signal={aiSignal}
                        loading={aiLoading}
                        error={aiError}
                        onRegenerate={loadAI}
                        confidenceThreshold={confidenceThreshold}
                    />
                    <NewsFeed />
                </aside>
            </main>

            <footer className="px-4 py-4 border-t border-[#262626] mt-4">
                <p className="text-[10px] font-mono text-gray-600 uppercase tracking-[0.2em]">
                    For research only · Not financial advice · Powered by Claude Sonnet 4.5 + TwelveData
                </p>
            </footer>
        </div>
    );
}
