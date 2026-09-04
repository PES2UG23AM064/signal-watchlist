import { AlertTriangle, GitCompareArrows, Sparkles } from "lucide-react";
import { timeAgo } from "../format.js";

// Data-state chips: no number is shown without saying how fresh it is and where it came from. Freshness
// and source are independent axes; nothing here is a forecast. One shape (.chip), colour carries the meaning.

const FRESH = {
  // "fresh", not "live": this axis is the quote's age, not whether the feed is real.
  fresh: { cls: "chip-fresh", dot: "bg-fresh", word: "fresh" },
  delayed: { cls: "chip-delayed", dot: "bg-delayed", word: "delayed" },
  stale: { cls: "chip-stale", dot: "bg-stale", word: "stale" },
  no_data: { cls: "chip-neutral", dot: null, word: "no data" },
};

export function FreshnessBadge({ provenance }) {
  const f = provenance?.freshness || "no_data";
  const s = FRESH[f] || FRESH.no_data;
  return (
    <span
      className={`chip ${s.cls}`}
      title={
        s.dot
          ? `This price is stamped ${new Date(provenance.event_time).toLocaleTimeString()} — ${Math.round(provenance.age_seconds)}s old. "${f}" comes from that age, never assumed.`
          : "No price has arrived for this symbol yet."
      }
    >
      {s.dot && <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />}
      {s.word}
      {s.dot && <span className="num opacity-70 font-normal">{timeAgo(provenance.event_time)}</span>}
    </span>
  );
}

// Shown when the data isn't a live feed, so "Market closed" next to moving prices never reads as a lie.
export function SourceBadge({ provenance }) {
  if (!provenance?.is_simulated) return null;
  return (
    <span className="chip chip-sim" title="These prices replay real market data. They are not a live exchange feed.">
      <Sparkles size={11} />
      simulated
    </span>
  );
}

export function QuarantineBadge({ provenance }) {
  const n = provenance?.quarantined_recent || 0;
  if (!n) return null;
  return (
    <span
      className="chip chip-stale"
      title={`We rejected ${n} bad price tick${n > 1 ? "s" : ""} in the last five minutes — impossible prices, jumps beyond the exchange's circuit limit, or timestamps from the future. The price you see was never affected.`}
    >
      <AlertTriangle size={11} />
      <span className="num">{n}</span> bad {n > 1 ? "prices" : "price"} rejected
    </span>
  );
}

// A second feed disagrees with the served price; show the disagreement rather than silently pick one.
export function DisputedBadge({ provenance }) {
  if (!provenance?.disputed || !provenance.dispute) return null;
  const d = provenance.dispute;
  return (
    <span
      className="chip chip-stale"
      title={`We're showing ₹${d.primary_price} from ${d.primary_source}. A second source says ₹${d.secondary_price} — ${d.divergence_pct}% apart. The second source is a cross-check and is never shown as the price.`}
    >
      <GitCompareArrows size={11} />
      sources disagree <span className="num">{d.divergence_pct}%</span>
    </span>
  );
}

// Reasons beyond the headline, which already carries the strongest one.
export function ExtraReasons({ reasons, lead, max = 2 }) {
  // A headline that joins several reasons would otherwise repeat them all as chips.
  const extra = (reasons || []).filter((r) => r !== lead && !(lead || "").includes(r));
  if (extra.length === 0) return null;
  const shown = extra.slice(0, max);
  const more = extra.length - shown.length;
  return (
    <div className="flex flex-wrap gap-1 mt-2">
      {shown.map((r) => (
        <span key={r} className="chip chip-delayed">{r}</span>
      ))}
      {more > 0 && (
        <span className="chip chip-neutral" title={extra.slice(max).join(" · ")}>
          +{more} more
        </span>
      )}
    </div>
  );
}

export function MarketPill({ market }) {
  if (!market) return null;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 sm:px-2.5 py-1 rounded-full
        whitespace-nowrap shrink-0 ${market.is_open ? "chip-up" : "chip-neutral"}`}
      title={`${market.label} — ${market.detail}`}
      aria-label={market.label}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${market.is_open ? "bg-up animate-pulse" : "bg-ink-4"}`} />
      {/* The top bar doesn't fit the full label on a phone; the dot and tooltip carry it. */}
      <span className="hidden sm:inline">{market.label}</span>
      <span className="sm:hidden">{market.is_open ? "Open" : "Closed"}</span>
    </span>
  );
}

// Shared by digest cards and watchlist rows so the labels sit in the same place and order everywhere.
// Freshness is always shown; the source chip is off where the page already names the source once.
export function ProvenanceChips({ provenance, source = true, children }) {
  return (
    <div className="flex items-center gap-1 flex-wrap">
      <FreshnessBadge provenance={provenance} />
      {source && <SourceBadge provenance={provenance} />}
      <QuarantineBadge provenance={provenance} />
      <DisputedBadge provenance={provenance} />
      {children}
    </div>
  );
}
