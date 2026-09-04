import { BellOff, Check, CheckCheck, ChevronDown, Layers, Radar, Undo2 } from "lucide-react";
import { useState } from "react";
import { inr, pct, displaySymbol, timeAgo } from "../../format.js";
import { ProvenanceChips, ExtraReasons, FreshnessBadge } from "../Badges.jsx";
import { SkeletonCard } from "../ui/Bits.jsx";

// Grouping is presentation only: every symbol stays in `changes`. Meaningful same-cohort, same-direction
// moves fold into one card; "moving alone" symbols never fold. Non-meaningful moves are still listed as
// one quiet line each, so nothing is hidden but a 0.01% drift never gets a card.
function groupChanges(changes) {
  const alone = changes.filter((c) => c.moving_alone);
  const quiet = changes.filter((c) => !c.moving_alone && !c.signal.is_meaningful);
  const rest = changes.filter((c) => !c.moving_alone && c.signal.is_meaningful);
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
  return { alone, groups, singles, quiet };
}

function QuietList({ rows, onExplain }) {
  if (rows.length === 0) return null;
  return (
    <div className="pt-1">
      <div className="eyebrow mb-2">Also moved · nothing unusual</div>
      <ul className="card divide-y divide-line overflow-hidden">
        {rows.map((c) => {
          const up = c.change_since_seen.direction === "up";
          const reasons = (c.signal.reasons || []).filter((r) => !/^(Diverging from|Moving alone)/.test(r));
          return (
            <li
              key={c.symbol}
              onClick={() => onExplain(c.symbol)}
              className="px-3.5 py-2 flex items-center justify-between gap-3 cursor-pointer hover:bg-surface2/70 transition-colors"
            >
              <div className="min-w-0 flex items-center gap-2 flex-wrap">
                <span className="text-sm font-medium text-ink">{displaySymbol(c.symbol)}</span>
                {reasons.map((r) => <span key={r} className="chip chip-neutral">{r}</span>)}
                {c.provenance?.freshness !== "fresh" && <FreshnessBadge provenance={c.provenance} />}
              </div>
              <div className="shrink-0 text-right">
                <span className={`num text-sm font-semibold ${up ? "text-up" : "text-down"}`}>{pct(c.change_since_seen.pct)}</span>
                <span className="num text-2xs text-ink-4 ml-2">{inr(c.price)}</span>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// A pack move gets one card; expanding shows every member, so grouping never hides a symbol.
function GroupCard({ members, cohort, renderCard }) {
  const [expanded, setExpanded] = useState(false);
  const med = median(members.map((m) => m.change_since_seen.pct));
  const up = med >= 0;
  const anyMeaningful = members.some((m) => m.signal.is_meaningful);
  const corr = cohort?.mean_corr;
  return (
    <article className={`card overflow-hidden ${anyMeaningful ? "" : "opacity-70"}`}>
      <div className="p-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Layers size={15} className="text-cohort shrink-0" />
            <h3 className="font-semibold text-ink">
              <span className="num">{members.length}</span> moving together
            </h3>
          </div>
          <p className="text-sm text-ink-3 mt-1 truncate">
            {members.map((m) => displaySymbol(m.symbol)).join(" · ")}
          </p>
          <span
            className="chip chip-cohort mt-2"
            title="Mean correlation of these symbols' daily returns over the past year, from real candles — no sector table. They tend to move as one, so this is the sector, not one stock's news."
          >
            {corr != null ? <>moved as one all year · <span className="num">{corr.toFixed(2)}</span> correlated</> : "sector move, not one stock's news"}
          </span>
        </div>
        <div className="text-right shrink-0">
          <div className={`num text-lg font-semibold ${up ? "text-up" : "text-down"}`}>{pct(med)}</div>
          <div className="text-2xs text-ink-4">median move</div>
        </div>
      </div>
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="w-full px-4 py-2 text-xs text-ink-3 hover:text-ink hover:bg-surface2
                   border-t border-line flex items-center justify-center gap-1 transition-colors"
      >
        {expanded ? "Collapse" : `Show all ${members.length}`}
        <ChevronDown size={14} className={`transition-transform ${expanded ? "rotate-180" : ""}`} />
      </button>
      {expanded && <div className="p-3 pt-0 space-y-2 bg-surface2/60">{members.map(renderCard)}</div>}
    </article>
  );
}

export default function DigestFeed({ changes, cohorts, mixedSources, loading, onMarkAllSeen, onSnooze, onExplain }) {
  const snoozed = changes.filter((c) => c.snoozed_until);
  const { alone, groups, singles, quiet } = groupChanges(changes.filter((c) => !c.snoozed_until));
  const folded = groups.reduce((n, g) => n + g.length, 0);
  const cohortById = new Map((cohorts || []).map((c) => [c.id, c]));

  const renderCard = (c) => {
    const up = c.change_since_seen.direction === "up";
    const tone = c.moving_alone
      ? "bg-alone"
      : c.signal.is_meaningful
      ? up ? "bg-up" : "bg-down"
      : "bg-surface2";
    // The server's "Diverging from X, …" / "Moving alone — …" reasons become the one alone-chip and are
    // not repeated as reason chips underneath.
    const pairMatch = (c.signal.reasons || []).map((r) => /^Diverging from (\S+),/.exec(r)).find(Boolean);
    const divergingFrom = pairMatch ? pairMatch[1] : null;
    const cardReasons = (c.signal.reasons || []).filter((r) => !/^(Diverging from|Moving alone)/.test(r));
    // No chip when the headline itself is the alone-reason.
    const aloneChip = c.moving_alone && !/^(Diverging from|Moving alone)/.test(c.headline);
    // Headlines are shared plain English; the sigma chip is what tells a 5σ move from a 2σ one at a glance.
    const sigma = c.signal.explain?.sigma_move;
    const mins = Math.round((c.signal.explain?.elapsed_seconds || 0) / 60);
    return (
      <article
        key={c.symbol}
        onClick={() => onExplain(c.symbol)}
        className={`card relative overflow-hidden pl-4 pr-4 py-3.5 cursor-pointer transition-shadow
                    hover:shadow-lift ${c.signal.is_meaningful || c.moving_alone ? "" : "opacity-70"}`}
      >
        <span className={`absolute left-0 top-0 bottom-0 w-[3px] ${tone}`} aria-hidden />

        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="font-semibold text-ink tracking-tight">{displaySymbol(c.symbol)}</h3>
              {aloneChip && (
                <span
                  className="chip chip-alone"
                  title="This stock is moving very differently from the symbols it usually moves with — this is about IT, not the market."
                >
                  {divergingFrom ? `diverging from ${divergingFrom}` : "moving alone"}
                </span>
              )}
              {c.signal.is_meaningful && sigma != null && sigma >= 1 && (
                <span
                  className="chip chip-neutral num"
                  title={`${sigma.toFixed(1)}× the move you'd expect from this stock in ${mins} minutes, after removing the market's move`}
                >
                  {sigma.toFixed(1)}× usual
                </span>
              )}
            </div>
            {/* Clamped so a long headline can't push the card's actions off the fold. */}
            <p className="text-sm font-medium text-ink-2 mt-1 leading-snug line-clamp-3">{c.headline}</p>
            <ExtraReasons reasons={cardReasons} lead={c.headline} />
            {c.impact_inr != null && (
              <div
                className={`inline-flex items-baseline gap-1.5 mt-2.5 px-2.5 py-1.5 rounded-lg ${
                  c.impact_inr >= 0 ? "bg-up/10" : "bg-down/10"
                }`}
                title="Shares you hold × the price change since you last looked"
              >
                <span className={`num text-sm font-semibold ${c.impact_inr >= 0 ? "text-up" : "text-down"}`}>
                  {c.impact_inr >= 0 ? "+" : "−"}{inr(Math.abs(c.impact_inr))}
                </span>
                <span className="text-2xs text-ink-3">
                  on your <span className="num">{c.quantity}</span> shares
                </span>
              </div>
            )}
          </div>
          <div
            className="text-right shrink-0"
            title={`You last looked ${timeAgo(c.last_seen.event_time)}, when it was ${inr(c.last_seen.price)}. Everything on this card is measured from there.`}
          >
            <div className="num font-semibold text-ink">{inr(c.price)}</div>
            <div className={`num text-sm font-semibold ${up ? "text-up" : "text-down"}`}>{pct(c.change_since_seen.pct)}</div>
            <div className="text-2xs text-ink-3 mt-0.5 leading-tight">
              since you saw <span className="num">{inr(c.last_seen.price)}</span>
            </div>
            <div className="text-2xs text-ink-4 leading-tight">
              <span className="num">{timeAgo(c.last_seen.event_time)}</span>
            </div>
          </div>
        </div>

        <div className="flex items-end justify-between gap-2 mt-3">
          {/* Freshness is per price; the source is said once in the summary strip unless sources are mixed. */}
          <ProvenanceChips provenance={c.provenance} source={!!mixedSources} />
          <div className="flex items-center gap-0.5 shrink-0" onClick={(e) => e.stopPropagation()}>
            {c.snoozed_until ? (
              <button onClick={() => onSnooze(c.symbol, 0)} className="btn btn-ghost btn-sm" title="Stop snoozing">
                <Undo2 size={14} /> Unsnooze
              </button>
            ) : (
              <button
                onClick={() => onSnooze(c.symbol, 60)}
                className="btn btn-ghost btn-sm"
                title="Hold out of 'needs attention' for an hour — it stays listed"
              >
                <BellOff size={14} />
                <span className="sr-only md:not-sr-only">Snooze</span>
              </button>
            )}
            <button onClick={() => onExplain(c.symbol)} className="btn btn-soft btn-sm">
              Why?
            </button>
          </div>
        </div>

        {c.snoozed_until && (
          <p className="text-2xs text-ink-4 mt-2">
            Snoozed until{" "}
            <span className="num">
              {new Date(c.snoozed_until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>{" "}
            — still listed, not counted.
          </p>
        )}
      </article>
    );
  };

  return (
    <section aria-labelledby="digest-heading">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h2 id="digest-heading" className="text-base font-semibold text-ink tracking-tight">
            While you were away
          </h2>
          {/* The attention count lives in the summary strip, not here. */}
          {!loading && changes.length > 0 && (
            <p className="text-xs text-ink-3 mt-0.5">
              Ranked by how unusual, not by size
              {groups.length > 0 && (
                <span className="text-ink-4">
                  {" · "}<span className="num">{folded}</span> folded into{" "}
                  <span className="num">{groups.length}</span> sector {groups.length === 1 ? "move" : "moves"}
                </span>
              )}
            </p>
          )}
        </div>
        {changes.length > 0 && (
          <button onClick={onMarkAllSeen} className="btn btn-outline btn-sm shrink-0">
            <CheckCheck size={14} /> Mark all seen
          </button>
        )}
      </div>

      {loading ? (
        <div className="space-y-2.5">
          <SkeletonCard />
          <SkeletonCard lines={1} />
        </div>
      ) : changes.length === 0 ? (
        <div className="card px-6 py-10 text-center">
          <div className="w-11 h-11 rounded-full bg-brand/10 text-brand-dark dark:text-brand mx-auto flex items-center justify-center">
            <Check size={22} strokeWidth={2.5} />
          </div>
          <p className="font-semibold text-ink mt-3">You're all caught up</p>
          <p className="text-sm text-ink-3 mt-1">Nothing has moved since you last looked.</p>
          <p className="text-2xs text-ink-4 mt-3 inline-flex items-center gap-1.5">
            <Radar size={12} /> Still watching — you'll see anything unusual here first.
          </p>
        </div>
      ) : (
        <div className="space-y-2.5">
          {alone.map(renderCard)}
          {groups.map((g, i) => (
            <GroupCard key={`g${i}`} members={g} cohort={cohortById.get(g[0].cohort_id)} renderCard={renderCard} />
          ))}
          {singles.map(renderCard)}
          {alone.length + groups.length + singles.length === 0 && quiet.length > 0 && (
            <div className="card px-6 py-6 text-center">
              <p className="font-semibold text-ink">Nothing needs your attention</p>
              <p className="text-sm text-ink-3 mt-1">
                <span className="num">{quiet.length}</span> {quiet.length === 1 ? "symbol" : "symbols"} moved, none unusually.
              </p>
            </div>
          )}
          <QuietList rows={quiet} onExplain={onExplain} />
          {snoozed.length > 0 && (
            <div className="pt-2">
              <div className="eyebrow mb-2 flex items-center gap-1.5">
                <BellOff size={11} /> Snoozed · <span className="num">{snoozed.length}</span>
              </div>
              <div className="space-y-2.5 opacity-60">{snoozed.map(renderCard)}</div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
