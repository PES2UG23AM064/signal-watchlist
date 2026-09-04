import { useState } from "react";
import { History } from "lucide-react";
import { api } from "../../api.js";
import { displaySymbol } from "../../format.js";
import Drawer from "../ui/Drawer.jsx";

// Demo-only: injects faults through the real ingestion path. Only reachable when the data is simulated.
const KINDS = [
  ["garbage", "Price = 0", "Impossible price → quarantined; the served price doesn't move."],
  ["jump", "+35% tick", "Beyond NSE's 20% circuit band → quarantined as bad/conflicting data."],
  ["future", "+1h timestamp", "Would pin itself as 'latest' forever → quarantined."],
  ["stale", "Upstream down", "Pauses this symbol's feed ~2.5 min: watch the badge go live → delayed → stale, then recover."],
  ["conflict", "2nd feed disagrees", "Records a second-feed quote 5% off → 'disputed' badge with both prices; the secondary is never served."],
];

export default function DemoDrawer({ open, onClose, symbols, onRewind, onDone }) {
  const [symbol, setSymbol] = useState(symbols[0] || "");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  // The selected symbol can be removed from the watchlist while the drawer is closed.
  const active = symbols.includes(symbol) ? symbol : symbols[0] || "";

  async function fire(kind) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.inject(active, kind);
      setMsg(
        kind === "stale"
          ? `Feed for ${displaySymbol(active)} paused — watch its freshness badge age.`
          : kind === "conflict"
          ? `Second feed says ${r.injected_price} → ${displaySymbol(active)} is now DISPUTED; served price unchanged (${r.served_price_still}).`
          : `Injected ${kind} (${r.injected_price}) → ${r.quarantined ? "QUARANTINED" : "accepted"}; served price ${
              r.served_unchanged ? "unchanged" : "changed"
            } (${r.served_price_still}).`
      );
      onDone && onDone();
    } catch (e) {
      setMsg(e.message || "Injection failed");
    } finally {
      setBusy(false);
    }
  }

  async function rewind() {
    setBusy(true);
    setMsg(null);
    try {
      await onRewind();
      setMsg("Your baselines were moved back 15 minutes — check 'While you were away'.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Demo tools"
      subtitle="Available because this feed is simulated. Never offered on live data."
    >
      <div className="space-y-6">
        <div>
          <div className="eyebrow mb-2">Time travel</div>
          <button onClick={rewind} disabled={busy} className="btn btn-outline btn-md w-full justify-start">
            <History size={16} className="text-ink-4" />
            Pretend I last looked 15 minutes ago
          </button>
          <p className="text-2xs text-ink-4 mt-2 leading-relaxed">
            Re-creates your snapshots from the simulator's deterministic history, so "While you were away" has
            something real to rank on demand.
          </p>
        </div>

        {symbols.length > 0 && (
          <div>
            <div className="flex items-center justify-between gap-2 mb-2">
              <span className="eyebrow">Break it on purpose</span>
              <label htmlFor="fault-symbol" className="sr-only">Symbol to break</label>
              <select
                id="fault-symbol"
                value={active}
                onChange={(e) => setSymbol(e.target.value)}
                className="text-xs border border-line rounded-md px-2 py-1 bg-surface"
              >
                {symbols.map((s) => (
                  <option key={s} value={s}>{displaySymbol(s)}</option>
                ))}
              </select>
            </div>
            <p className="text-2xs text-ink-4 mb-3 leading-relaxed">
              Sends bad data through the real ingestion path — the same validators that run in production — and
              shows you what got refused.
            </p>
            <div className="grid grid-cols-2 gap-2">
              {KINDS.map(([kind, label, hint]) => (
                <button
                  key={kind}
                  disabled={busy}
                  onClick={() => fire(kind)}
                  title={hint}
                  className="btn btn-outline text-left flex-col items-start gap-0.5 px-3 py-2.5"
                >
                  <span className="text-xs font-medium text-ink">{label}</span>
                  <span className="text-2xs text-ink-4 font-normal">{kind}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {msg && (
          <div className="text-xs text-ink-2 bg-surface2 border border-line rounded-lg px-3 py-2.5 leading-relaxed">
            {msg}
          </div>
        )}
      </div>
    </Drawer>
  );
}
