import { useEffect, useState } from "react";
import {
    TrendingUp, TrendingDown, Minus, Sparkles, RefreshCw, Loader2,
    Clock, Shield, Newspaper, DollarSign, Layers, Activity,
} from "lucide-react";

function fmt(n, d = 2) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    return Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function formatDuration(ms) {
    if (ms <= 0) return "EXPIRED";
    const s = Math.floor(ms / 1000);
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
}

const actionStyle = {
    BUY: { color: "#00B86B", bg: "rgba(0,184,107,0.12)", Icon: TrendingUp, border: "#00B86B" },
    SELL: { color: "#FF2A55", bg: "rgba(255,42,85,0.12)", Icon: TrendingDown, border: "#FF2A55" },
    HOLD: { color: "#D4AF37", bg: "rgba(212,175,55,0.12)", Icon: Minus, border: "#D4AF37" },
};

const strengthStyle = {
    VERY_STRONG: { label: "VERY STRONG", color: "#00B86B", bg: "rgba(0,184,107,0.15)" },
    STRONG: { label: "STRONG", color: "#10B981", bg: "rgba(16,185,129,0.15)" },
    MODERATE: { label: "MODERATE", color: "#D4AF37", bg: "rgba(212,175,55,0.15)" },
    WEAK: { label: "WEAK", color: "#FF2A55", bg: "rgba(255,42,85,0.15)" },
};

const sentimentColor = (s) =>
    s === "BULLISH" ? "#00B86B" : s === "BEARISH" ? "#FF2A55" : "#A3A3A3";

function CountdownBadge({ expiresAt }) {
    const [now, setNow] = useState(Date.now());
    useEffect(() => {
        const id = window.setInterval(() => setNow(Date.now()), 1000);
        return () => window.clearInterval(id);
    }, []);
    if (!expiresAt) return null;
    const exp = new Date(expiresAt).getTime();
    const remaining = exp - now;
    const expired = remaining <= 0;
    return (
        <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-sm border font-mono text-[10px] uppercase tracking-[0.1em]"
            style={{
                color: expired ? "#FF2A55" : "#D4AF37",
                background: expired ? "rgba(255,42,85,0.1)" : "rgba(212,175,55,0.08)",
                borderColor: expired ? "rgba(255,42,85,0.4)" : "rgba(212,175,55,0.3)",
            }}
            data-testid="signal-expiry"
        >
            <Clock className="w-3 h-3" />
            {expired ? "Expired" : `Expires in ${formatDuration(remaining)}`}
        </div>
    );
}

function StepRow({ icon: Icon, title, text, testId }) {
    if (!text) return null;
    return (
        <div className="flex items-start gap-2 py-2 border-b border-[#262626]/60 last:border-b-0" data-testid={testId}>
            <Icon className="w-3.5 h-3.5 text-[#D4AF37] mt-0.5 shrink-0" />
            <div className="flex-1">
                <div className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.15em] mb-0.5">{title}</div>
                <div className="text-xs text-gray-200 leading-relaxed">{text}</div>
            </div>
        </div>
    );
}

export default function AISignalCard({ signal, loading, error, onRegenerate, confidenceThreshold = 0 }) {
    const action = (signal?.action || "HOLD").toUpperCase();
    const style = actionStyle[action] || actionStyle.HOLD;
    const Icon = style.Icon;

    const tps = signal ? [
        { label: "TP1", v: signal.tp1 ?? signal.take_profit },
        { label: "TP2", v: signal.tp2 },
        { label: "TP3", v: signal.tp3 },
    ].filter((x) => x.v != null) : [];

    const strength = signal?.signal_strength ? strengthStyle[signal.signal_strength] : null;
    const belowThreshold = signal && signal.confidence < confidenceThreshold;
    const steps = signal?.analysis_steps;

    return (
        <div
            className="border border-[#262626] rounded-sm overflow-hidden relative"
            style={{ background: "linear-gradient(180deg, #141414 0%, #0F0F0F 100%)" }}
            data-testid="ai-signal-card"
        >
            <div className="flex items-center justify-between px-4 py-2 border-b border-[#262626]">
                <div className="flex items-center gap-2">
                    <Sparkles className="w-3.5 h-3.5 text-[#D4AF37]" />
                    <span className="text-[10px] font-mono text-[#D4AF37] uppercase tracking-[0.2em]">
                        Deep AI Signal · Claude 4.5
                    </span>
                </div>
                <button
                    onClick={onRegenerate}
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
                    <p className="mt-3 text-xs font-mono text-gray-500 uppercase tracking-[0.2em]">
                        Running deep analysis…
                    </p>
                    <p className="mt-1 text-[10px] font-mono text-gray-600 tracking-[0.15em]">
                        Trend · Momentum · News · Macro
                    </p>
                </div>
            )}

            {signal && (
                <div className="p-4 space-y-4">
                    {/* Below-threshold warning */}
                    {belowThreshold && (
                        <div
                            className="px-3 py-2 border border-[#FF2A55]/40 bg-[#FF2A55]/10 rounded-sm text-[10px] font-mono uppercase tracking-[0.15em] text-[#FF2A55]"
                            data-testid="below-threshold-warning"
                        >
                            ⚠ Below your {confidenceThreshold}% threshold — do not act.
                        </div>
                    )}

                    {/* Action + confidence */}
                    <div
                        className="flex items-center justify-between px-4 py-3 rounded-sm border"
                        style={{ background: style.bg, borderColor: style.border, opacity: belowThreshold ? 0.5 : 1 }}
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
                            <div className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em]">
                                Confidence
                            </div>
                            <div className="font-mono font-bold text-3xl" style={{ color: style.color }}>
                                {signal.confidence}%
                            </div>
                        </div>
                    </div>

                    {/* Strength badge + Expiry + R:R */}
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                        {strength && (
                            <div
                                className="flex items-center gap-1.5 px-2 py-1 rounded-sm border font-mono text-[10px] uppercase tracking-[0.1em]"
                                style={{ color: strength.color, background: strength.bg, borderColor: `${strength.color}55` }}
                                data-testid="signal-strength"
                            >
                                <Shield className="w-3 h-3" />
                                {strength.label}
                            </div>
                        )}
                        <CountdownBadge expiresAt={signal.expires_at} />
                        {signal.risk_reward != null && (
                            <div
                                className="flex items-center gap-1.5 px-2 py-1 rounded-sm border border-[#262626] font-mono text-[10px] uppercase tracking-[0.1em] text-gray-300"
                                data-testid="risk-reward"
                            >
                                <Activity className="w-3 h-3 text-[#D4AF37]" />
                                R:R {fmt(signal.risk_reward, 1)}
                            </div>
                        )}
                    </div>

                    {/* Entry & SL */}
                    <div className="grid grid-cols-2 gap-2">
                        <div className="bg-[#1A1A1A] border border-[#262626] p-2 rounded-sm">
                            <div className="text-[9px] font-mono text-gray-500 uppercase tracking-[0.15em]">Entry</div>
                            <div className="font-mono font-bold text-base text-white" data-testid="ai-entry">
                                {fmt(signal.entry)}
                            </div>
                        </div>
                        <div className="bg-[#1A1A1A] border border-[#262626] p-2 rounded-sm">
                            <div className="text-[9px] font-mono text-[#FF2A55] uppercase tracking-[0.15em]">Stop Loss</div>
                            <div className="font-mono font-bold text-base text-[#FF2A55]" data-testid="ai-sl">
                                {fmt(signal.stop_loss)}
                            </div>
                        </div>
                    </div>

                    {/* TP ladder */}
                    {tps.length > 0 && (
                        <div className={`grid gap-2 ${tps.length === 3 ? "grid-cols-3" : tps.length === 2 ? "grid-cols-2" : "grid-cols-1"}`}>
                            {tps.map((tp) => (
                                <div
                                    key={tp.label}
                                    className="bg-[#0F1F15] border border-[#00B86B]/30 p-2 rounded-sm"
                                    data-testid={`ai-${tp.label.toLowerCase()}`}
                                >
                                    <div className="text-[9px] font-mono text-[#00B86B] uppercase tracking-[0.15em]">
                                        {tp.label}
                                    </div>
                                    <div className="font-mono font-bold text-sm text-[#00B86B]">{fmt(tp.v)}</div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Confluence row: HTF / LTF / News / DXY */}
                    <div className="grid grid-cols-2 gap-2 pt-1">
                        <div className="bg-[#0F0F0F] border border-[#262626] p-2 rounded-sm" data-testid="confluence-htf">
                            <div className="text-[9px] font-mono text-gray-500 uppercase tracking-[0.15em] flex items-center gap-1">
                                <Layers className="w-3 h-3" /> Higher TF
                            </div>
                            <div className="font-mono font-bold text-xs mt-1" style={{ color: sentimentColor(signal.higher_tf_trend) }}>
                                {signal.higher_tf_trend || "—"}
                            </div>
                        </div>
                        <div className="bg-[#0F0F0F] border border-[#262626] p-2 rounded-sm" data-testid="confluence-ltf">
                            <div className="text-[9px] font-mono text-gray-500 uppercase tracking-[0.15em] flex items-center gap-1">
                                <Layers className="w-3 h-3" /> Lower TF
                            </div>
                            <div className="font-mono font-bold text-xs mt-1" style={{ color: sentimentColor(signal.lower_tf_momentum) }}>
                                {signal.lower_tf_momentum || "—"}
                            </div>
                        </div>
                        <div className="bg-[#0F0F0F] border border-[#262626] p-2 rounded-sm" data-testid="confluence-news">
                            <div className="text-[9px] font-mono text-gray-500 uppercase tracking-[0.15em] flex items-center gap-1">
                                <Newspaper className="w-3 h-3" /> News
                            </div>
                            <div className="font-mono font-bold text-xs mt-1" style={{ color: sentimentColor(signal.news_sentiment) }}>
                                {signal.news_sentiment || "—"}
                            </div>
                        </div>
                        <div className="bg-[#0F0F0F] border border-[#262626] p-2 rounded-sm" data-testid="confluence-dxy">
                            <div className="text-[9px] font-mono text-gray-500 uppercase tracking-[0.15em] flex items-center gap-1">
                                <DollarSign className="w-3 h-3" /> DXY
                            </div>
                            <div className="font-mono font-bold text-xs mt-1 text-white">
                                {signal.dxy_price != null
                                    ? `${fmt(signal.dxy_price)} (${signal.dxy_change_pct >= 0 ? "+" : ""}${fmt(signal.dxy_change_pct, 2)}%)`
                                    : "n/a"}
                            </div>
                        </div>
                    </div>

                    {/* Verdict & reasoning */}
                    <div>
                        <div className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em] mb-1">Verdict</div>
                        <div className="text-sm text-white font-medium leading-snug" data-testid="ai-summary">
                            {signal.summary}
                        </div>
                    </div>

                    {/* Analysis steps */}
                    {steps && (
                        <div className="border-t border-[#262626] pt-3">
                            <div className="text-[10px] font-mono text-[#D4AF37] uppercase tracking-[0.2em] mb-1">
                                Multi-Factor Breakdown
                            </div>
                            <div className="divide-y divide-[#262626]/60">
                                <StepRow icon={Layers} title="Trend" text={steps.trend} testId="step-trend" />
                                <StepRow icon={Activity} title="Momentum" text={steps.momentum} testId="step-momentum" />
                                <StepRow icon={Newspaper} title="News" text={steps.news} testId="step-news" />
                                <StepRow icon={DollarSign} title="Macro" text={steps.macro} testId="step-macro" />
                                <StepRow icon={Sparkles} title="Verdict" text={steps.verdict} testId="step-verdict" />
                            </div>
                        </div>
                    )}

                    {!steps && signal.reasoning && (
                        <div>
                            <div className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em] mb-1">Reasoning</div>
                            <div className="text-xs text-gray-300 leading-relaxed" data-testid="ai-reasoning">
                                {signal.reasoning}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
