import { useEffect, useState } from "react";
import { fetchSignalHistory } from "@/lib/api";
import { Loader2 } from "lucide-react";

function fmt(n, d = 2) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function timeAgo(iso) {
    if (!iso) return "";
    const t = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (t < 60) return `${t}s`;
    if (t < 3600) return `${Math.floor(t / 60)}m`;
    if (t < 86400) return `${Math.floor(t / 3600)}h`;
    return `${Math.floor(t / 86400)}d`;
}

const actionColor = (a) => a === "BUY" ? "#00B86B" : a === "SELL" ? "#FF2A55" : "#D4AF37";

export default function SignalHistory({ refreshKey }) {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        fetchSignalHistory(10)
            .then((d) => { if (alive) setItems(d.signals || []); })
            .catch(() => {})
            .finally(() => { if (alive) setLoading(false); });
        return () => { alive = false; };
    }, [refreshKey]);

    return (
        <div className="bg-[#121212] border border-[#262626] rounded-sm h-full" data-testid="signal-history">
            <div className="flex items-center justify-between px-4 py-2 border-b border-[#262626]">
                <span className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.2em]">Signal History</span>
                <span className="text-[10px] font-mono text-gray-600">Last 10</span>
            </div>
            {loading && (
                <div className="p-6 text-center"><Loader2 className="w-4 h-4 mx-auto animate-spin text-[#D4AF37]" /></div>
            )}
            {!loading && items.length === 0 && (
                <div className="p-4 text-xs font-mono text-gray-500">No signals yet. Generate one above.</div>
            )}
            {!loading && items.length > 0 && (
                <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="text-[10px] font-mono text-gray-500 uppercase tracking-[0.1em] border-b border-[#262626]">
                                <th className="px-3 py-2 text-left">Time</th>
                                <th className="px-3 py-2 text-left">TF</th>
                                <th className="px-3 py-2 text-left">Action</th>
                                <th className="px-3 py-2 text-right">Conf</th>
                                <th className="px-3 py-2 text-right">Entry</th>
                                <th className="px-3 py-2 text-right">SL</th>
                                <th className="px-3 py-2 text-right">TP</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map((s, i) => (
                                <tr key={s.id || i} className="border-b border-[#262626]/60 hover:bg-[#1A1A1A]">
                                    <td className="px-3 py-2 font-mono text-xs text-gray-400">{timeAgo(s.created_at)} ago</td>
                                    <td className="px-3 py-2 font-mono text-xs text-gray-300">{s.timeframe}</td>
                                    <td className="px-3 py-2 font-mono font-bold text-xs" style={{ color: actionColor(s.action) }}>
                                        {s.action}
                                    </td>
                                    <td className="px-3 py-2 font-mono text-xs text-right text-white">{s.confidence}%</td>
                                    <td className="px-3 py-2 font-mono text-xs text-right text-white">{fmt(s.entry)}</td>
                                    <td className="px-3 py-2 font-mono text-xs text-right text-[#FF2A55]">{fmt(s.stop_loss)}</td>
                                    <td className="px-3 py-2 font-mono text-xs text-right text-[#00B86B]">{fmt(s.take_profit)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
