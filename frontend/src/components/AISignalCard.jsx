import { useEffect, useState } from "react";
import { fetchAISignal } from "@/lib/api";
import { TrendingUp, TrendingDown, Minus, Sparkles, RefreshCw, Loader2 } from "lucide-react";

function fmt(n, d = 2) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    return Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

const actionStyle = {
    BUY: { color: "#00B86B", bg: "rgba(0,184,107,0.12)", Icon: TrendingUp, border: "#00B86B" },
    SELL: { color: "#FF2A55", bg: "rgba(255,42,85,0.12)", Icon: TrendingDown, border: "#FF2A55" },
    HOLD: { color: "#D4AF37", bg: "rgba(212,175,55,0.12)", Icon: Minus, border: "#D4AF37" },
};

export default function AISignalCard({ interval, refreshKey }) {
    const [signal, setSignal] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            const s = await fetchAISignal(interval);
            setSignal(s);
        } catch (e) {
            setError(e?.response?.data?.detail || e.message || "AI analysis failed");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); /* eslint-disable-next-line */ }, [interval, refreshKey]);

    const action = (signal?.action || "HOLD").toUpperCase();
    const style = actionStyle[action] || actionStyle.HOLD;
    const Icon = style.Icon;

    return (
        <div
            className="border border-[#262626] rounded-sm overflow-hidden relative"
            style={{ background: "linear-gradient(180deg, #141414 0%, #0F0F0F 100%)" }}
            data-testid="ai-signal-card"
        >
            <div className="flex items-center justify-between px-4 py-2 border-b border-[#262626]">
                <div className="flex items-center gap-2">
                    <Sparkles className="w-3.5 h-3.5 text-[#D4AF37]" />
                    <span className="text-[10px] font-mono text-[#D4AF37] uppercase tracking-[0.2em]">AI Signal · Claude 4.5</span>
                </div>
                <button
                    onClick={load}
                    disabled={loading}
                    data-testid="regen-ai-btn"
                    className="text-[10px] font-mono uppercase text-gray-400 hover:text-[#D4AF37] disabled:opacity-50"
                >
                    {loading ? <Loader2 className="w-3 h-3 inline animate-spin" /> : <RefreshCw className="w-3 h-3 inline" />}
                </button>
            </div>

            {error && (
                <div className="p-4 text-xs font-mono text-[#FF2A55]" data-testid="ai-error">
                    {error}
                </div>
            )}

            {!signal && !error && loading && (
                <div className="p-6 text-center">
                    <Loader2 className="w-6 h-6 mx-auto animate-spin text-[#D4AF37]" />
                    <p className="mt-3 text-xs font-mono text-gray-500 uppercase tracking-[0.2em]">Analyzing market…</p>
                </div>
            )}

            {signal && (
                <div className="p-4 space-y-4">
                    <div
                        className="flex items-center justify-between px-4 py-3 rounded-sm border"
                        style={{ background: style.bg, borderColor: style.border }}
                        data-testid="ai-action-display"
                    >
                        <div className="flex items-center gap-3">
                            <Icon className="w-7 h-7" style={{ color: style.color }} />
                            <div>
                                <div className="font-black tracking-tighter text-3xl" style={{ color: style.color }}>
                                    {action}
                                </div>
                                <div className="text-[10px] font-mono text-gray-400 uppercase tracking-[0.2em]">
                                    {signal.timeframe} · {signal.sentiment}
                                </div>
                            </div>
                        </div>
                        <div className="text-right">
                            <div className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em]">Confidence</div>
                            <div className="font-mono font-bold text-3xl" style={{ color: style.color }}>
                                {signal.confidence}%
                            </div>
                        </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                        <div className="bg-[#1A1A1A] border border-[#262626] p-2 rounded-sm">
                            <div className="text-[9px] font-mono text-gray-500 uppercase tracking-[0.15em]">Entry</div>
                            <div className="font-mono font-bold text-sm text-white" data-testid="ai-entry">{fmt(signal.entry)}</div>
                        </div>
                        <div className="bg-[#1A1A1A] border border-[#262626] p-2 rounded-sm">
                            <div className="text-[9px] font-mono text-[#FF2A55] uppercase tracking-[0.15em]">Stop</div>
                            <div className="font-mono font-bold text-sm text-[#FF2A55]" data-testid="ai-sl">{fmt(signal.stop_loss)}</div>
                        </div>
                        <div className="bg-[#1A1A1A] border border-[#262626] p-2 rounded-sm">
                            <div className="text-[9px] font-mono text-[#00B86B] uppercase tracking-[0.15em]">Target</div>
                            <div className="font-mono font-bold text-sm text-[#00B86B]" data-testid="ai-tp">{fmt(signal.take_profit)}</div>
                        </div>
                    </div>

                    <div>
                        <div className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em] mb-1">Verdict</div>
                        <div className="text-sm text-white font-medium leading-snug" data-testid="ai-summary">
                            {signal.summary}
                        </div>
                    </div>

                    <div>
                        <div className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em] mb-1">Reasoning</div>
                        <div className="text-xs text-gray-300 leading-relaxed" data-testid="ai-reasoning">
                            {signal.reasoning}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
