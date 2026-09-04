import { useEffect, useState } from "react";
import { Check, Loader2, Plus, Trash2 } from "lucide-react";
import { inr, pct, displaySymbol } from "../../format.js";
import { ProvenanceChips, ExtraReasons } from "../Badges.jsx";

// Optional holding size; held symbols rank by rupees at stake.
function QtyInput({ item, onSetQuantity }) {
  const [val, setVal] = useState(item.quantity ?? "");
  const [editing, setEditing] = useState(false);
  const id = `qty-${item.symbol}`;
  // A quantity set on another device shows up on the next refresh, but never overwrites what's being typed.
  useEffect(() => {
    if (!editing) setVal(item.quantity ?? "");
  }, [item.quantity, editing]);
  async function commit() {
    const n = val === "" ? null : Number(val);
    if (n === item.quantity || (n === null && item.quantity == null)) return;
    if (n !== null && (!Number.isFinite(n) || n < 0)) return;
    await onSetQuantity(item.symbol, n);
  }
  return (
    <span className="inline-flex items-center gap-1.5 min-w-0">
      <label htmlFor={id} className="text-2xs text-ink-4">Qty</label>
      <input
        id={id}
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onFocus={() => setEditing(true)}
        onBlur={() => { setEditing(false); commit(); }}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        inputMode="numeric"
        placeholder="—"
        className="w-12 px-1.5 py-1 rounded-md border border-line text-2xs text-ink
                   text-center focus:border-brand/50"
        title="Shares you hold (optional). Held symbols are ranked by rupees at stake."
      />
      {item.exposure_inr != null && (
        <span className="num text-2xs text-ink-4 truncate">≈ {inr(item.exposure_inr)}</span>
      )}
    </span>
  );
}

// Reasons and "why?" live in the digest, so a symbol that surfaced is explained once. The exception is a
// symbol with a reason the digest can't show (e.g. flat price, unusual volume): it keeps its reason line
// here so nothing is hidden.
function Row({ item, inDigest, mixedSources, onSeen, onRemove, onSetQuantity, onExplain }) {
  const ch = item.change_since_seen;
  const up = ch && ch.direction === "up";
  const attention = !!(item.signal?.is_meaningful && !item.snoozed_until);
  const orphanReasons = !inDigest && item.signal?.reasons?.length ? item.signal.reasons : null;
  const explainable = !!(orphanReasons && ch && item.last_seen && item.price != null);
  return (
    <li className="px-3.5 py-3 hover:bg-surface2/70 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-ink tracking-tight leading-none flex items-center gap-1.5">
            {attention && (
              <span
                className="w-1.5 h-1.5 rounded-full bg-alone shrink-0"
                title="In your digest — something unusual since you last looked"
                aria-label="needs attention"
              />
            )}
            {displaySymbol(item.symbol)}
          </h3>
          <div className="mt-1.5">
            <ProvenanceChips provenance={item.provenance} source={!!mixedSources} />
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="num font-semibold text-ink">{item.price == null ? "—" : inr(item.price)}</div>
          {ch ? (
            <div
              className={`num text-xs font-semibold ${
                up ? "text-up" : ch.direction === "down" ? "text-down" : "text-ink-4"
              }`}
            >
              {pct(ch.pct)} <span className="text-ink-4 font-normal">since seen</span>
            </div>
          ) : (
            <div className="text-2xs text-ink-4">watching from now</div>
          )}
        </div>
      </div>

      {orphanReasons && (
        <>
          <p className="text-xs font-medium text-ink-2 mt-2 leading-snug">{orphanReasons[0]}</p>
          <ExtraReasons reasons={orphanReasons} lead={orphanReasons[0]} max={1} />
        </>
      )}

      <div className="flex items-center justify-between gap-2 mt-2.5">
        <QtyInput item={item} onSetQuantity={onSetQuantity} />
        <div className="flex items-center gap-1 shrink-0">
          {explainable && (
            <button onClick={() => onExplain(item.symbol)} className="btn btn-ghost btn-sm">
              Why?
            </button>
          )}
          <button
            onClick={() => onSeen(item.symbol)}
            className="btn btn-sm chip-brand "
            title="Mark this as your new baseline — future changes are measured from here"
          >
            <Check size={13} /> Seen
          </button>
          <button
            onClick={() => onRemove(item.symbol)}
            aria-label={`Remove ${displaySymbol(item.symbol)}`}
            title="Remove from watchlist"
            className="btn btn-ghost p-1.5 hover:text-down"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
    </li>
  );
}

export default function WatchlistPanel({ items, digestSymbols, mixedSources, loading, onAdd, onSeen, onRemove, onSetQuantity, onExplain }) {
  const [symbol, setSymbol] = useState("");
  const [pending, setPending] = useState(0);
  const inDigest = new Set(digestSymbols || []);

  // An add takes a second or more (it fetches a year of real candles). The box is freed immediately so
  // the next symbol can be typed — a disabled submit button silently drops Enter, and users type faster
  // than the network. Adds may overlap; the API is idempotent. A rejected symbol comes back into the box
  // so it can be fixed, unless something else has been typed since.
  async function add(e) {
    e.preventDefault();
    const s = symbol.trim();
    if (!s) return;
    setSymbol("");
    setPending((n) => n + 1);
    try {
      if (!(await onAdd(s))) setSymbol((cur) => cur || s);
    } finally {
      setPending((n) => n - 1);
    }
  }

  return (
    <section aria-labelledby="watchlist-heading">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h2 id="watchlist-heading" className="text-base font-semibold text-ink tracking-tight">
          Watchlist
          {items.length > 0 && <span className="num text-ink-4 font-normal"> · {items.length}</span>}
        </h2>
      </div>

      <form onSubmit={add} className="flex gap-2 mb-3">
        <label htmlFor="add-symbol" className="sr-only">Add a symbol</label>
        <input
          id="add-symbol"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Add a symbol — e.g. RELIANCE"
          autoCapitalize="characters"
          autoComplete="off"
          className="field grow min-w-0"
        />
        <button disabled={!symbol.trim()} className="btn btn-primary btn-md shrink-0" title={pending ? `Adding ${pending}…` : "Add"}>
          {pending ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} Add
        </button>
      </form>

      {loading ? (
        <div className="card divide-y divide-line">
          {[0, 1, 2].map((i) => (
            <div key={i} className="px-3.5 py-4 flex justify-between gap-4">
              <div className="skeleton h-4 w-20" />
              <div className="skeleton h-4 w-16" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="card px-5 py-8 text-center">
          <p className="text-sm font-medium text-ink">Nothing on the list yet</p>
          <p className="text-xs text-ink-3 mt-1">
            Add a stock above. From that moment we remember what you saw, and tell you what changed.
          </p>
        </div>
      ) : (
        <ul className="card divide-y divide-line overflow-hidden">
          {items.map((item) => (
            <Row
              key={item.symbol}
              item={item}
              inDigest={inDigest.has(item.symbol)}
              mixedSources={mixedSources}
              onSeen={onSeen}
              onRemove={onRemove}
              onSetQuantity={onSetQuantity}
              onExplain={onExplain}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
