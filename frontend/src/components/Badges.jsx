import { timeAgo } from "../format.js";

// Honest data-state surface: the app never shows a number without saying how fresh it is and
// whether it's live or simulated. These three axes are independent (see backend app/market.py).

const FRESH_STYLES = {
  fresh: "bg-emerald-50 text-emerald-700",
  delayed: "bg-amber-50 text-amber-700",
  stale: "bg-red-50 text-red-700",
  no_data: "bg-slate-100 text-slate-500",
};

export function FreshnessBadge({ provenance }) {
  const f = provenance?.freshness || "no_data";
  const label =
    f === "fresh"
      ? `live · ${timeAgo(provenance.event_time)}`
      : f === "delayed"
      ? `delayed · ${timeAgo(provenance.event_time)}`
      : f === "stale"
      ? `stale · ${timeAgo(provenance.event_time)}`
      : "no data";
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded ${FRESH_STYLES[f]}`}>
      {f !== "no_data" && (
        <span className={`w-1.5 h-1.5 rounded-full ${f === "fresh" ? "bg-emerald-500" : f === "delayed" ? "bg-amber-500" : "bg-red-500"}`} />
      )}
      {label}
    </span>
  );
}

// Loud "simulated" chip when the data isn't a live feed — so "NSE closed" next to moving prices
// never reads as the app lying.
export function SourceBadge({ provenance }) {
  if (!provenance?.is_simulated) return null;
  return (
    <span className="text-[11px] px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 font-medium" title="Prices are simulated, not a live market feed">
      simulated
    </span>
  );
}

export function MarketPill({ market }) {
  if (!market) return null;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full ${
        market.is_open ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"
      }`}
      title={market.detail}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${market.is_open ? "bg-emerald-500" : "bg-slate-400"}`} />
      {market.label}
    </span>
  );
}
