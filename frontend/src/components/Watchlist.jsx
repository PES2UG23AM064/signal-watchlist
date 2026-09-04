import { useState } from "react";
import { inr, pct, displaySymbol } from "../format.js";
import { DisputedBadge, FreshnessBadge, QuarantineBadge, SourceBadge } from "./Badges.jsx";

// "How much do you hold?" — optional; held symbols rank by rupees at stake.
function QtyInput({ item, onSetQuantity }) {
  const [val, setVal] = useState(item.quantity ?? "");
  async function commit() {
    const n = val === "" ? null : Number(val);
    if (n === item.quantity || (n === null && item.quantity == null)) return;
    if (n !== null && (!Number.isFinite(n) || n < 0)) return;
    await onSetQuantity(item.symbol, n);
  }
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-slate-500">
      hold
      <input
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        inputMode="numeric"
        placeholder="qty"
        className="w-14 px-1.5 py-0.5 rounded border border-slate-200 text-[11px] text-slate-700 focus:outline-none focus:ring-1 focus:ring-brand/40"
        title="Shares you hold (optional). Held symbols are ranked by rupees at stake."
      />
      {item.exposure_inr != null && <span className="text-slate-400">≈ {inr(item.exposure_inr)}</span>}
    </span>
  );
}

function Row({ item, onSeen, onRemove, onSetQuantity }) {
  const ch = item.change_since_seen;
  const up = ch && ch.direction === "up";
  return (
    <div className="bg-white rounded-2xl border border-slate-100 p-4 flex items-center justify-between">
      <div className="min-w-0">
        <div className="font-semibold text-slate-900">{displaySymbol(item.symbol)}</div>
        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
          <FreshnessBadge provenance={item.provenance} />
          <SourceBadge provenance={item.provenance} />
          <QuarantineBadge provenance={item.provenance} />
          <DisputedBadge provenance={item.provenance} />
        </div>
        <div className="mt-1.5"><QtyInput item={item} onSetQuantity={onSetQuantity} /></div>
        {item.signal && item.signal.reasons.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {item.signal.reasons.map((r) => (
              <span key={r} className="text-[11px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-800">{r}</span>
            ))}
          </div>
        )}
      </div>
      <div className="flex items-center gap-3">
        <div className="text-right">
          <div className="font-semibold text-slate-900">{item.price == null ? "—" : inr(item.price)}</div>
          {ch ? (
            <div className={`text-sm font-medium ${up ? "text-up" : ch.direction === "down" ? "text-down" : "text-slate-400"}`}>
              {pct(ch.pct)} <span className="text-slate-400 font-normal">since seen</span>
            </div>
          ) : (
            <div className="text-xs text-slate-400">watching from now</div>
          )}
        </div>
        <div className="flex flex-col gap-1">
          <button
            onClick={() => onSeen(item.symbol)}
            className="text-xs px-2 py-1 rounded-md bg-brand/10 text-brand-dark font-medium hover:bg-brand/20"
            title="Mark this as your new baseline"
          >
            Seen
          </button>
          <button
            onClick={() => onRemove(item.symbol)}
            className="text-xs px-2 py-1 rounded-md text-slate-400 hover:text-down"
          >
            Remove
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Watchlist({ items, onAdd, onSeen, onRemove, onSetQuantity }) {
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);

  async function add(e) {
    e.preventDefault();
    const s = symbol.trim();
    if (!s) return;
    setBusy(true);
    try {
      await onAdd(s);
      setSymbol("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-2">Watchlist</h2>

      <form onSubmit={add} className="flex gap-2 mb-3">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Add a symbol (e.g. RELIANCE)"
          autoCapitalize="characters"
          className="flex-1 px-3 py-2.5 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-brand/40"
        />
        <button
          disabled={busy}
          className="px-4 py-2.5 rounded-lg bg-slate-900 text-white font-medium disabled:opacity-60"
        >
          Add
        </button>
      </form>

      {items.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-100 p-5 text-center text-sm text-slate-500">
          Your watchlist is empty. Add a stock to start tracking what changes.
        </div>
      ) : (
        <div className="space-y-2.5">
          {items.map((item) => (
            <Row key={item.symbol} item={item} onSeen={onSeen} onRemove={onRemove} onSetQuantity={onSetQuantity} />
          ))}
        </div>
      )}
    </section>
  );
}
