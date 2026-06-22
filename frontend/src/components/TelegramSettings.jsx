import { useEffect, useState } from "react";
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Send, Bell, ExternalLink, Loader2, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { fetchTelegramConfig, updateTelegramConfig, sendTelegramTest } from "@/lib/api";

export default function TelegramSettings() {
    const [open, setOpen] = useState(false);
    const [config, setConfig] = useState(null);
    const [botToken, setBotToken] = useState("");
    const [chatId, setChatId] = useState("");
    const [enabled, setEnabled] = useState(false);
    const [strongOnly, setStrongOnly] = useState(true);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);

    useEffect(() => {
        if (!open) return;
        fetchTelegramConfig().then((cfg) => {
            setConfig(cfg);
            setChatId(cfg.chat_id || "");
            setEnabled(cfg.enabled || false);
            setStrongOnly(cfg.strong_only ?? true);
        }).catch(() => {});
    }, [open]);

    const handleSave = async () => {
        setSaving(true);
        try {
            const payload = { enabled, strong_only: strongOnly, chat_id: chatId };
            if (botToken.trim()) payload.bot_token = botToken.trim();
            const updated = await updateTelegramConfig(payload);
            setConfig(updated);
            setBotToken(""); // clear input after save
            toast.success("Telegram settings saved");
        } catch (e) {
            toast.error(e?.response?.data?.detail || "Save failed");
        } finally {
            setSaving(false);
        }
    };

    const handleTest = async () => {
        setTesting(true);
        try {
            await sendTelegramTest();
            toast.success("Test message sent — check your Telegram!");
        } catch (e) {
            toast.error(e?.response?.data?.detail || "Test failed");
        } finally {
            setTesting(false);
        }
    };

    const isConfigured = config?.has_token && config?.chat_id;

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <button
                    data-testid="open-telegram-settings-btn"
                    className="text-xs font-mono uppercase tracking-wider px-3 py-1.5 border border-[#262626] hover:border-[#D4AF37] hover:text-[#D4AF37] transition-colors rounded-sm flex items-center gap-1"
                >
                    <Bell className="w-3 h-3" />
                    <span className="hidden sm:inline">Alerts</span>
                    {config?.enabled && (
                        <span className="w-1.5 h-1.5 rounded-full bg-[#00B86B] live-dot" />
                    )}
                </button>
            </DialogTrigger>
            <DialogContent
                className="bg-[#0F0F0F] border-[#262626] text-white max-w-lg max-h-[90vh] overflow-y-auto"
                data-testid="telegram-settings-modal"
            >
                <DialogHeader>
                    <DialogTitle className="font-black tracking-tighter text-2xl">
                        Telegram <span className="text-[#D4AF37]">Alerts</span>
                    </DialogTitle>
                    <p className="text-xs font-mono text-gray-400 leading-relaxed">
                        Get an instant push notification on your phone every time a
                        <span className="text-[#00B86B] font-bold"> STRONG + DOUBLE CONFIRMED </span>
                        signal is generated.
                    </p>
                </DialogHeader>

                <div className="space-y-4 pt-2">
                    {/* Setup instructions */}
                    <div className="bg-[#1A1A1A] border border-[#262626] p-3 rounded-sm text-xs font-mono space-y-2 text-gray-300">
                        <p className="font-bold text-[#D4AF37] uppercase tracking-[0.15em] text-[10px]">
                            One-time setup
                        </p>
                        <ol className="space-y-1.5 list-decimal pl-4">
                            <li>
                                Open Telegram → search{" "}
                                <a
                                    href="https://t.me/BotFather"
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-[#D4AF37] hover:underline inline-flex items-center gap-0.5"
                                >
                                    @BotFather <ExternalLink className="w-3 h-3" />
                                </a>{" "}
                                → send <code className="text-[#00B86B]">/newbot</code> → copy the bot token
                            </li>
                            <li>
                                Search{" "}
                                <a
                                    href="https://t.me/userinfobot"
                                    target="_blank"
                                    rel="noreferrer"
                                    className="text-[#D4AF37] hover:underline inline-flex items-center gap-0.5"
                                >
                                    @userinfobot <ExternalLink className="w-3 h-3" />
                                </a>{" "}
                                → send any message → copy your <b>chat ID</b>
                            </li>
                            <li>Send <code className="text-[#00B86B]">/start</code> to YOUR bot (required once)</li>
                            <li>Paste both below and hit Save</li>
                        </ol>
                    </div>

                    {/* Bot Token */}
                    <div className="space-y-1.5">
                        <Label htmlFor="bot-token" className="text-[10px] font-mono uppercase tracking-[0.2em] text-gray-400">
                            Bot Token
                            {config?.has_token && (
                                <span className="ml-2 text-[#00B86B] inline-flex items-center gap-1">
                                    <CheckCircle2 className="w-3 h-3" /> Set
                                </span>
                            )}
                        </Label>
                        <Input
                            id="bot-token"
                            data-testid="bot-token-input"
                            type="password"
                            placeholder={config?.has_token ? config.bot_token_masked : "123456789:ABCdefGhIJKlmnoPQR…"}
                            value={botToken}
                            onChange={(e) => setBotToken(e.target.value)}
                            className="font-mono text-sm bg-[#1A1A1A] border-[#262626] text-white"
                        />
                    </div>

                    {/* Chat ID */}
                    <div className="space-y-1.5">
                        <Label htmlFor="chat-id" className="text-[10px] font-mono uppercase tracking-[0.2em] text-gray-400">
                            Chat ID
                        </Label>
                        <Input
                            id="chat-id"
                            data-testid="chat-id-input"
                            placeholder="123456789"
                            value={chatId}
                            onChange={(e) => setChatId(e.target.value)}
                            className="font-mono text-sm bg-[#1A1A1A] border-[#262626] text-white"
                        />
                    </div>

                    {/* Toggles */}
                    <div className="bg-[#1A1A1A] border border-[#262626] rounded-sm p-3 space-y-3">
                        <div className="flex items-center justify-between">
                            <div>
                                <Label className="text-sm text-white font-medium">Enable alerts</Label>
                                <p className="text-[10px] font-mono text-gray-500 mt-0.5">
                                    Master switch
                                </p>
                            </div>
                            <Switch
                                checked={enabled}
                                onCheckedChange={setEnabled}
                                data-testid="alert-enabled-switch"
                            />
                        </div>
                        <div className="flex items-center justify-between border-t border-[#262626] pt-3">
                            <div>
                                <Label className="text-sm text-white font-medium">Strong signals only</Label>
                                <p className="text-[10px] font-mono text-gray-500 mt-0.5">
                                    Only DOUBLE CONFIRMED + STRONG/VERY STRONG
                                </p>
                            </div>
                            <Switch
                                checked={strongOnly}
                                onCheckedChange={setStrongOnly}
                                data-testid="strong-only-switch"
                            />
                        </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2 pt-2">
                        <Button
                            onClick={handleSave}
                            disabled={saving}
                            data-testid="save-telegram-btn"
                            className="flex-1 bg-[#D4AF37] hover:bg-[#C09F2C] text-black font-mono uppercase tracking-wider"
                        >
                            {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
                            Save Settings
                        </Button>
                        <Button
                            onClick={handleTest}
                            disabled={testing || !isConfigured}
                            data-testid="test-telegram-btn"
                            variant="outline"
                            className="border-[#262626] text-white hover:bg-[#1A1A1A] hover:text-[#D4AF37] font-mono uppercase tracking-wider"
                        >
                            {testing ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Send className="w-3 h-3 mr-1" />}
                            Test
                        </Button>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
}
