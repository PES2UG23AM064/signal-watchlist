import { useState } from "react";
import { api } from "../api.js";
import { displaySymbol } from "../format.js";

// Demo-only: inject faults through the REAL ingestion path and watch the app refuse to lie.
// Shown only when data is simulated (never on a live feed).
const KINDS = [
  ["garbage", "price = 0", "Impossible price → quarantined; the served price doesn't move."],
  ["jump", "+35% tick", "Beyond NSE's 20% circuit band → quarantined as bad/conflicting data."],
  ["future", "+1h timestamp", "Would pin itself as 'latest' forever → quarantined."],
  ["stale", "upstream down", "Pauses this symbol's feed ~2.5 min: watch the badge go live → delayed → stale, then recover."],
  ["conflict", "2nd feed disagrees", "Records a second-feed quote 5% off → 'disputed' badge with both prices; the secondary is never served."],
];

export default function FaultPanel({ symbols, onDone }) {
  const [symbol, setSymbol] = useState(symbols[0] || "");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!symbols.length) return null;

  async function fire(kind) {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.inject(symbol, kind);
      setMsg(
        kind === "stale"
          ? `Feed for ${displaySymbol(symbol)} paused — watch its freshness badge age.`
          : kind === "conflict"
          ? `Second feed says ${r.injected_price} → ${displaySymbol(symbol)} is now DISPUTED; served price unchanged (${r.served_price_still}).`
          : `Injected ${kind} (${r.injected_price}) → ${r.quarantined ? "QUARANTINED" : "accepted"}; served price ${r.served_unchanged ? "unchanged" : "changed"} (${r.served_price_still}).`
      );
      onDone && onDone();
    } catch (e) {
      setMsg(e.message || "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-6">
      <div className="bg-white rounded-2xl border border-dashed border-slate-200 p-4">
        <div className="flex items-center justify-between gap-2 mb-2">
          <div>
            <div className="text-sm font-semibold text-slate-800">Break it on purpose</div>
            <div className="text-xs text-slate-500">Demo: send bad data through the real ingestion path and watch it get refused.</div>
          </div>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className="text-xs border border-slate-200 rounded-md px-2 py-1">
            {symbols.map((s) => <option key={s} value={s}>{displaySymbol(s)}</option>)}
          </select>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {KINDS.map(([kind, label, hint]) => (
            <button key={kind} disabled={busy} onClick={() => fire(kind)} title={hint}
              className="text-xs px-2 py-2 rounded-lg border border-slate-200 hover:border-slate-400 text-left disabled:opacity-50">
              <div className="font-medium text-slate-800">{label}</div>
              <div className="text-slate-400">{kind}</div>
            </button>
          ))}
        </div>
        {msg && <div className="text-xs text-slate-600 mt-2 bg-slate-50 rounded-md px-2 py-1.5">{msg}</div>}
      </div>
    </section>
  );
}
