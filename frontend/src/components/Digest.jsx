import { useState } from "react";
import { inr, pct, displaySymbol } from "../format.js";
import { FreshnessBadge, SourceBadge } from "./Badges.jsx";

// The one learned tag: P(entering an active period). Only shown when clearly elevated vs the ~31% base
// rate, and always framed as an outlook on ACTIVITY, never direction.
function ActivityTag({ activity }) {
  if (!activity || activity.probability < 0.4) return null;
  return (
    <span
      className="text-[11px] px-1.5 py-0.5 rounded bg-sky-50 text-sky-700"
      title={`Model ${activity.version}: historically, days like this were followed by an unusually active period ${Math.round(activity.probability * 100)}% of the time (base rate ~31%). Says nothing about direction.`}
    >
      likely active · {Math.round(activity.probability * 100)}%
    </span>
  );
}

// Explainability: every number behind the score. Nothing is a black box.
function Explain({ e, peerZ }) {
  const rows = [
    ["Move since you looked", `${e.move_pct >= 0 ? "+" : ""}${e.move_pct.toFixed(2)}%`],
    ["Market-adjusted move", `${e.market_adjusted_pct >= 0 ? "+" : ""}${e.market_adjusted_pct.toFixed(2)}%`,
      e.beta != null ? `(β ${e.beta} vs NIFTY removed)` : "(no index context)"],
    ["In units of its own volatility", `${e.sigma_move.toFixed(1)}σ`,
      `(this stock moves ${e.sigma_daily_pct}%/day, scaled by √time over ${Math.round(e.elapsed_seconds / 60)} min)`],
    ["Volume vs 20-day average", `${e.vol_ratio.toFixed(1)}×`],
    ["52-week level", e.crossed ? `crossed its ${e.crossed}` : "not crossed"],
  ];
  if (peerZ != null) rows.push(["vs its co-movement peers", `${Math.abs(peerZ).toFixed(1)}σ ${peerZ >= 0 ? "above" : "below"} the pack`]);
  return (
    <div className="mt-3 pt-3 border-t border-slate-100 text-xs space-y-1.5">
      {rows.map(([k, v, note]) => (
        <div key={k} className="flex justify-between gap-3">
          <span className="text-slate-500">{k}</span>
          <span className="text-right text-slate-800 font-medium">
            {v} {note && <span className="text-slate-400 font-normal">{note}</span>}
          </span>
        </div>
      ))}
      <p className="text-slate-400 pt-1">
        Flags are fixed thresholds (≥2σ, ≥1.5× volume, 52w break). Ranking is how unusual this was — not a prediction.
      </p>
    </div>
  );
}

// Co-movement grouping is PRESENTATION ONLY: every symbol is still in `changes`; here we just fold
// same-cohort, same-direction pack moves into one card, and always leave "moving alone" symbols out.
function groupChanges(changes) {
  const alone = changes.filter((c) => c.moving_alone);
  const rest = changes.filter((c) => !c.moving_alone);
  const packs = new Map();
  const singles = [];
  for (const c of rest) {
    if (c.cohort_id == null) { singles.push(c); continue; }
    const k = `${c.cohort_id}|${c.change_since_seen.direction}`;
    if (!packs.has(k)) packs.set(k, []);
    packs.get(k).push(c);
  }
  const groups = [];
  for (const members of packs.values()) {
    if (members.length >= 2) groups.push(members);
    else singles.push(members[0]);
  }
  return { alone, groups, singles };
}

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function GroupCard({ members, renderCard }) {
  const [expanded, setExpanded] = useState(false);
  const med = median(members.map((m) => m.change_since_seen.pct));
  const up = med >= 0;
  const anyMeaningful = members.some((m) => m.signal.is_meaningful);
  return (
    <div className={`bg-white rounded-2xl border p-4 ${anyMeaningful ? "border-slate-200 shadow-sm" : "border-slate-100 opacity-75"}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold text-slate-900">{members.length} moving together</div>
          <div className="text-sm text-slate-600 mt-0.5">
            {members.map((m) => displaySymbol(m.symbol)).join(", ")} — moved as a pack, not one stock's news
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className={`font-semibold ${up ? "text-up" : "text-down"}`}>{pct(med)}</div>
          <div className="text-[11px] text-slate-400">median</div>
        </div>
      </div>
      <div className="flex items-center justify-between mt-2.5">
        <span
          className="text-[11px] px-1.5 py-0.5 rounded bg-violet-50 text-violet-700"
          title="These symbols' daily returns are highly correlated over the past year — they tend to move as one."
        >
          co-movement cohort
        </span>
        <button onClick={() => setExpanded(!expanded)} className="text-xs text-slate-400 hover:text-slate-700">
          {expanded ? "collapse" : `show ${members.length}`}
        </button>
      </div>
      {expanded && <div className="mt-3 space-y-2.5">{members.map(renderCard)}</div>}
    </div>
  );
}

export default function Digest({ changes, onMarkAllSeen }) {
  const [open, setOpen] = useState(null);
  const count = changes.filter((c) => c.signal.is_meaningful).length;
  const { alone, groups, singles } = groupChanges(changes);

  if (changes.length === 0) {
    return (
      <section className="mb-6">
        <div className="bg-white rounded-2xl border border-slate-100 p-5 text-center">
          <p className="text-slate-800 font-medium">You're all caught up</p>
          <p className="text-sm text-slate-500 mt-1">Nothing has moved since you last looked.</p>
        </div>
      </section>
    );
  }

  const renderCard = (c) => {
    const up = c.change_since_seen.direction === "up";
    const sig = c.signal;
    const isOpen = open === c.symbol;
    return (
      <div
        key={c.symbol}
        className={`bg-white rounded-2xl border p-4 ${
          c.moving_alone ? "border-amber-300 shadow-sm" : sig.is_meaningful ? "border-slate-200 shadow-sm" : "border-slate-100 opacity-75"
        }`}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-semibold text-slate-900 flex items-center gap-2">
              {displaySymbol(c.symbol)}
              {c.moving_alone && (
                <span
                  className="text-[11px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 font-medium"
                  title="This stock is moving very differently from the symbols it usually moves with — this is about IT, not the market."
                >
                  moving alone
                </span>
              )}
            </div>
            <div className="text-sm text-slate-600 mt-0.5">{c.headline}</div>
          </div>
          <div className="text-right shrink-0">
            <div className="font-semibold text-slate-900">{inr(c.price)}</div>
            <div className={`text-sm font-medium ${up ? "text-up" : "text-down"}`}>{pct(c.change_since_seen.pct)}</div>
          </div>
        </div>

        <div className="flex items-center justify-between mt-2.5 gap-2">
          <div className="flex items-center gap-1.5 flex-wrap">
            <FreshnessBadge provenance={c.provenance} />
            <SourceBadge provenance={c.provenance} />
            <ActivityTag activity={sig.activity} />
          </div>
          <button onClick={() => setOpen(isOpen ? null : c.symbol)} className="text-xs text-slate-400 hover:text-slate-700 shrink-0">
            {isOpen ? "hide" : "why?"}
          </button>
        </div>
        <div className="text-xs text-slate-400 mt-1.5">you last saw {inr(c.last_seen.price)}</div>

        {isOpen && <Explain e={sig.explain} peerZ={c.peer_residual_z} />}
      </div>
    );
  };

  return (
    <section className="mb-6">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide">While you were away</h2>
        <button onClick={onMarkAllSeen} className="text-xs text-brand font-medium hover:underline">
          Mark all seen
        </button>
      </div>

      <p className="text-sm text-slate-600 mb-3">
        {count > 0 ? (
          <>
            <span className="font-semibold text-slate-900">{count}</span> of {changes.length}{" "}
            {count === 1 ? "needs" : "need"} your attention
            {groups.length > 0 && (
              <span className="text-slate-400"> · {groups.reduce((n, g) => n + g.length, 0)} folded into {groups.length} pack {groups.length === 1 ? "move" : "moves"}</span>
            )}
          </>
        ) : (
          <>{changes.length} moved, but nothing unusual</>
        )}
      </p>

      <div className="space-y-2.5">
        {alone.map(renderCard)}
        {groups.map((g, i) => <GroupCard key={`g${i}`} members={g} renderCard={renderCard} />)}
        {singles.map(renderCard)}
      </div>
    </section>
  );
}
