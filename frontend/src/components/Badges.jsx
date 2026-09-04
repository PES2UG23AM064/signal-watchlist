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

// The quarantine made visible: bad ticks were rejected recently (stored for audit, never served).
export function QuarantineBadge({ provenance }) {
  const n = provenance?.quarantined_recent || 0;
  if (!n) return null;
  return (
    <span
      className="text-[11px] px-1.5 py-0.5 rounded bg-red-50 text-red-700"
      title={`${n} bad tick${n > 1 ? "s" : ""} (impossible price, >20% single-tick jump, or future timestamp) were quarantined in the last 5 minutes. The price you see was never affected.`}
    >
      ⚠ {n} bad tick{n > 1 ? "s" : ""} quarantined
    </span>
  );
}

// Cross-source reconciliation made visible: a second feed disagrees with the served price. We show the
// disagreement rather than silently picking one; the primary is what's served.
export function DisputedBadge({ provenance }) {
  if (!provenance?.disputed || !provenance.dispute) return null;
  const d = provenance.dispute;
  return (
    <span
      className="text-[11px] px-1.5 py-0.5 rounded bg-orange-50 text-orange-700"
      title={`Served (${d.primary_source}): ₹${d.primary_price}. Second feed (${d.secondary_source}): ₹${d.secondary_price} — ${d.divergence_pct}% apart. The secondary is a cross-check and is never served as the price.`}
    >
      ⚠ disputed · 2nd feed ₹{Number(d.secondary_price).toLocaleString("en-IN")} ({d.divergence_pct}% off)
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
