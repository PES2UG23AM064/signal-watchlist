import { useState } from "react";
import { inr, pct, displaySymbol, timeAgo } from "../format.js";

function Row({ item, onSeen, onRemove }) {
  const ch = item.change_since_seen;
  const up = ch && ch.direction === "up";
  return (
    <div className="bg-white rounded-2xl border border-slate-100 p-4 flex items-center justify-between">
      <div className="min-w-0">
        <div className="font-semibold text-slate-900">{displaySymbol(item.symbol)}</div>
        <div className="text-xs text-slate-400 mt-0.5">updated {timeAgo(item.event_time)}</div>
      </div>
      <div className="flex items-center gap-3">
        <div className="text-right">
          <div className="font-semibold text-slate-900">{inr(item.price)}</div>
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

export default function Watchlist({ items, onAdd, onSeen, onRemove }) {
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
            <Row key={item.symbol} item={item} onSeen={onSeen} onRemove={onRemove} />
          ))}
        </div>
      )}
    </section>
  );
}
