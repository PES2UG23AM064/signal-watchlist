import { displaySymbol } from "../../format.js";
import { Bar } from "../ui/Bits.jsx";

// Unsupervised co-movement cohorts: structure, stability and a counterfactual alert replay.

// Cells in the same cohort are ringed so the clustering can be checked against the correlation colour.
function Heatmap({ symbols, matrix, cohorts }) {
  const cohortOf = new Map();
  (cohorts || []).forEach((g, i) => g.forEach((s) => cohortOf.set(s, i)));
  const short = (s) => displaySymbol(s).slice(0, 4);

  return (
    <div className="overflow-x-auto no-scrollbar -mx-1 px-1">
      {/* Width scales with the universe so a phone scrolls sideways instead of collapsing to an unreadable grid. */}
      <div
        className="grid gap-[2px]"
        style={{
          gridTemplateColumns: `2.6rem repeat(${symbols.length}, minmax(0, 1fr))`,
          minWidth: `${42 + symbols.length * 17}px`,
        }}
      >
        <div />
        {symbols.map((s) => (
          <div key={s} className="h-12 flex items-end justify-center pb-1" title={displaySymbol(s)}>
            <span className="text-[9px] font-medium text-ink-3 [writing-mode:vertical-rl] rotate-180 leading-none">
              {short(s)}
            </span>
          </div>
        ))}
        {symbols.map((rowSym, i) => (
          <div key={rowSym} className="contents">
            <div className="text-[9px] font-medium text-ink-3 flex items-center justify-end pr-1.5 truncate">
              {short(rowSym)}
            </div>
            {symbols.map((colSym, j) => {
              const v = matrix[i][j];
              const same = i !== j && cohortOf.get(rowSym) != null && cohortOf.get(rowSym) === cohortOf.get(colSym);
              return (
                <div
                  key={colSym}
                  title={`${displaySymbol(rowSym)} vs ${displaySymbol(colSym)} — return correlation ${v.toFixed(2)}`}
                  className={`aspect-square rounded-[3px] ${same ? "ring-2 ring-cohort ring-inset" : ""}`}
                  style={{ backgroundColor: `rgb(var(--cohort) / ${i === j ? 0.14 : Math.max(0, v) * 0.9})` }}
                />
              );
            })}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2 mt-3">
        <span className="text-2xs text-ink-4">corr 0</span>
        <div
          className="h-1.5 grow rounded-full"
          style={{ background: "linear-gradient(90deg, rgb(var(--cohort) / 0.05), rgb(var(--cohort) / 0.9))" }}
        />
        <span className="text-2xs text-ink-4">1</span>
        <span className="ml-2 text-2xs text-cohort flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-[2px] ring-2 ring-cohort ring-inset" /> same cohort
        </span>
      </div>
    </div>
  );
}

export default function CohortLab({ report, userCohorts }) {
  if (!report) return null;
  const s = report.structure;
  const st = report.stability;
  const a = report.alert_counterfactual;
  const cm = s.corr_matrix;
  const multi = (s.cohorts || []).filter((g) => g.length > 1);
  const singles = (s.cohorts || []).filter((g) => g.length === 1).length;
  const userMulti = (userCohorts || []).filter((c) => c.members.length > 1);

  return (
    <section className="card p-4 md:p-5">
      <header className="mb-3">
        <h2 className="font-semibold text-ink">Who moves with whom</h2>
        <p className="text-xs text-ink-3 mt-1 leading-relaxed">
          We gave the model a year of daily returns and nothing else — no sector labels — and it worked out which
          stocks move as one. That's why a sector-wide move becomes a single card, and why a stock breaking away
          from its group gets pushed to the top. Grouping never hides a symbol.
        </p>
      </header>

      {/* Labelled as its own universe: this clustering runs on a different symbol set from the backtest. */}
      <div className="eyebrow mb-2">
        Cohort universe · <span className="num">{cm.symbols.length}</span> NSE large-caps ·{" "}
        <span className="num">{s.n_days}</span> days of returns
      </div>
      <Heatmap symbols={cm.symbols} matrix={cm.matrix} cohorts={s.cohorts} />

      <div className="flex flex-wrap gap-1.5 mt-4">
        {multi.map((g, i) => (
          <span key={i} className="chip chip-cohort">
            {g.map(displaySymbol).join(" · ")}
          </span>
        ))}
        {singles > 0 && (
          <span className="chip bg-surface2 text-ink-3">
            <span className="num">{singles}</span> move on their own
          </span>
        )}
      </div>

      <div className="mt-4 pt-4 border-t border-line space-y-3">
        <div>
          <div className="flex items-center justify-between text-xs mb-1">
            <span className="text-ink-3">Correlation within a cohort</span>
            <span className="num font-semibold text-ink">{s.mean_intra_cohort_corr}</span>
          </div>
          <Bar value={s.mean_intra_cohort_corr} color="bg-cohort" />
          <div className="flex items-center justify-between text-xs mt-2 mb-1">
            <span className="text-ink-3">…and across cohorts</span>
            <span className="num font-semibold text-ink-3">{s.mean_inter_cohort_corr}</span>
          </div>
          <Bar value={s.mean_inter_cohort_corr} color="bg-line-strong" />
        </div>

        <div className="grid grid-cols-2 gap-3 pt-1">
          <div className="rounded-lg bg-surface2 p-3">
            <div className="eyebrow">Out-of-sample stability</div>
            <div className="num text-sm font-semibold text-ink mt-1">{st.symbols_kept_grouping}</div>
            <div className="num text-2xs text-ink-4 mt-0.5">
              kept grouping · ARI {st.adjusted_rand_index}
            </div>
          </div>
          <div className="rounded-lg bg-surface2 p-3">
            <div className="eyebrow">Alerts over the year</div>
            <div className="num text-sm font-semibold text-ink mt-1">
              {a.cards_old_rule} → {a.cards_cohort_rule}
            </div>
            <div className="num text-2xs text-ink-4 mt-0.5">
              {a.reduction_pct}% fewer · {a.moving_alone_hidden_in_a_group} hidden
            </div>
          </div>
        </div>

        <p className="text-2xs text-ink-4 leading-relaxed">
          The grouping is strong; its half-year stability is only modest, which is why that number is on screen
          rather than buried. None of the{" "}
          <span className="num">{a.moving_alone_flags}</span> "moving alone" flags was ever swallowed by a group.
        </p>
      </div>

      {userMulti.length > 0 && (
        <div className="mt-4 pt-4 border-t border-line">
          <div className="eyebrow mb-2">Your watchlist's cohorts · computed for your symbols</div>
          <div className="flex flex-wrap gap-1.5">
            {userMulti.map((c) => (
              <span key={c.id} className="chip chip-cohort">
                {c.members.map(displaySymbol).join(" · ")}
                {c.mean_corr != null && <span className="num text-cohort/70">r={c.mean_corr}</span>}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
