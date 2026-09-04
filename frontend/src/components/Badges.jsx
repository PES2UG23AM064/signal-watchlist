import { AlertTriangle, GitCompareArrows, Sparkles } from "lucide-react";
import { timeAgo } from "../format.js";

// Honest data-state surface: the app never shows a number without saying how fresh it is and
// whether it's live or simulated. These three axes are independent (see backend app/market.py).
// One shape (.chip), colour carries the meaning, copy stays to two or three words.

const FRESH = {
  fresh: { cls: "chip-fresh", dot: "bg-fresh", word: "live" },
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

// Loud "simulated" chip when the data isn't a live feed — so "Market closed" next to moving prices
// never reads as the app lying.
export function SourceBadge({ provenance }) {
  if (!provenance?.is_simulated) return null;
  return (
    <span className="chip chip-sim" title="These prices replay real market data. They are not a live exchange feed.">
      <Sparkles size={11} />
      simulated
    </span>
  );
}

// The quarantine made visible — in customer words. The legend explains it in full.
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

// Cross-source reconciliation made visible: a second feed disagrees with the price we serve. We show
// the disagreement rather than silently picking one.
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

// NOTE: there is deliberately no "predicted probability" badge in this file. All three pre-registered
// hypotheses came back with confidence intervals straddling 0.5, so no learned model ships and nothing
// on a card is a forecast. The evidence for that decision lives in How it works.

// Additional reasons beyond the headline. The headline carries the strongest one in plain English.
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
      className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full
        whitespace-nowrap shrink-0 ${market.is_open ? "chip-up" : "chip-neutral"}`}
      title={market.detail}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${market.is_open ? "bg-up animate-pulse" : "bg-ink-4"}`} />
      {market.label}
    </span>
  );
}

// Provenance strip used by both the digest cards and the watchlist rows, so the honesty labels sit
// in the same place with the same order everywhere. Freshness is per price and always shown; the source
// chip can be turned off where the page already says it once (the watchlist, next to the summary strip).
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
