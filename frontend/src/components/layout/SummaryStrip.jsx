import { inr, inrShort, timeAgo } from "../../format.js";
import { Stat } from "../ui/Bits.jsx";

// The four numbers that answer "should I be looking at this right now?" — the only thing above the
// feed, deliberately one line tall. Each number appears here and nowhere else on the page.
export default function SummaryStrip({ items, changes, loading }) {
  const live = changes.filter((c) => !c.snoozed_until);
  const attention = live.filter((c) => c.signal.is_meaningful).length;
  const held = changes.filter((c) => c.impact_inr != null);
  const netImpact = held.reduce((s, c) => s + c.impact_inr, 0);
  // "Since you last looked" is the product's unit of time: the oldest baseline on the list is how long
  // you've been away from the symbol you've been away from longest.
  const baselines = items.filter((i) => i.last_seen?.event_time).map((i) => new Date(i.last_seen.event_time).getTime());
  const oldest = baselines.length ? Math.min(...baselines) : null;
  const worst = items.reduce((w, i) => {
    const rank = { fresh: 0, delayed: 1, stale: 2, no_data: 3 };
    return rank[i.provenance?.freshness] > rank[w] ? i.provenance.freshness : w;
  }, "fresh");
  const staleCount = items.filter((i) => i.provenance?.freshness !== "fresh").length;
  const simulated = items.some((i) => i.provenance?.is_simulated);

  // 2x2 on a phone, one row on anything wider; the 1px gap over the line colour draws every divider.
  const grid = "card grid grid-cols-2 md:grid-cols-4 gap-px bg-line overflow-hidden mb-5";
  const tile = "bg-surface px-3.5 md:px-4 py-3";

  if (loading) {
    return (
      <div className={grid}>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className={tile}>
            <div className="skeleton h-2.5 w-16" />
            <div className="skeleton h-5 w-12 mt-2" />
          </div>
        ))}
      </div>
    );
  }

  const dataLabel = { fresh: "All live", delayed: "Delayed", stale: "Stale", no_data: "No data" }[worst] || "—";

  return (
    <div className={grid}>
      <div className={tile}>
        <Stat
          label="Attention"
          value={attention}
          sub={live.length ? `of ${live.length} that moved` : "nothing moved"}
          tone={attention > 0 ? "default" : "muted"}
        />
      </div>
      <div className={tile}>
        <Stat
          label="Your P&L"
          value={held.length ? `${netImpact >= 0 ? "+" : "−"}${inrShort(Math.abs(netImpact))}` : "—"}
          sub={held.length ? "since you looked" : "add a quantity"}
          tone={held.length ? (netImpact >= 0 ? "up" : "down") : "muted"}
          title={
            held.length
              ? `Net ${inr(netImpact)} on the shares you hold, since your last baseline.`
              : "Tell us how many shares you hold and this becomes rupees."
          }
        />
      </div>
      <div className={tile}>
        <Stat
          label="Last looked"
          value={oldest ? timeAgo(new Date(oldest).toISOString()) : "—"}
          sub={oldest ? "changes are measured from here" : "mark something seen"}
          tone={oldest ? "default" : "muted"}
          title={
            oldest
              ? `Your oldest baseline is from ${new Date(oldest).toLocaleTimeString()}. "Mark seen" moves it to now.`
              : "Once you mark a symbol seen, everything after that is what we tell you about."
          }
        />
      </div>
      <div className={tile}>
        <Stat
          label="Data"
          value={dataLabel}
          sub={
            staleCount
              ? `${staleCount} not fresh${simulated ? " · simulated" : ""}`
              : simulated ? "simulated feed" : "live feed"
          }
          tone={worst === "fresh" ? "default" : "muted"}
          title="Worst freshness across your watchlist. Every price on this page carries its own age badge."
        />
      </div>
    </div>
  );
}
