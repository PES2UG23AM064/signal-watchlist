// Backtest receipts: three pre-registered hypotheses tested out-of-sample on real NSE candles. All came
// back inside the noise band, which is why the shipped ranking is purely descriptive.

const NOISE = 0.5;

function hasCi(ci) {
  return Array.isArray(ci) && ci.length === 2 && ci.every((x) => typeof x === "number");
}
function includesNoise(ci) {
  return hasCi(ci) && ci[0] <= NOISE && ci[1] >= NOISE;
}

// The verdict is read from the interval, never asserted here: if a re-run moves an interval off 0.5,
// the label moves with it.
function Verdict({ title, auc, ci, metrics }) {
  const noise = includesNoise(ci);
  const marginal = hasCi(ci) && !noise && ci[0] > NOISE && ci[0] - NOISE < 0.02;
  return (
    <div className="py-3.5 border-b border-line last:border-0">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-ink leading-snug">{title}</p>
        <span
          className={`chip shrink-0 ${
            noise || !hasCi(ci) ? "bg-surface2 text-ink-2" : "chip-delayed"
          }`}
        >
          {noise || !hasCi(ci) ? "No edge" : marginal ? "Marginal" : "Weak edge"}
        </span>
      </div>
      <dl className="flex flex-wrap gap-x-5 gap-y-1 mt-2">
        <div className="flex items-baseline gap-1.5">
          <dt className="text-2xs text-ink-4">AUC</dt>
          <dd className="num text-xs font-semibold text-ink-2">
            {auc}
            {hasCi(ci) && <span className="text-ink-4 font-normal"> (95% CI {ci[0]}–{ci[1]})</span>}
          </dd>
        </div>
        {metrics.filter(([, v]) => v != null).map(([k, v]) => (
          <div key={k} className="flex items-baseline gap-1.5">
            <dt className="text-2xs text-ink-4">{k}</dt>
            <dd className="num text-xs font-semibold text-ink-2">{v}</dd>
          </div>
        ))}
      </dl>
      {noise && (
        <p className="text-2xs text-ink-3 mt-1.5">
          The interval includes <span className="num">0.50</span> — indistinguishable from a coin flip at this
          sample size.
        </p>
      )}
      {marginal && (
        <p className="text-2xs text-ink-3 mt-1.5">
          The interval clears <span className="num">0.50</span> by{" "}
          <span className="num">{(ci[0] - NOISE).toFixed(3)}</span> — a hair, on overlapping label windows.
          Reported, not shipped.
        </p>
      )}
    </div>
  );
}

function NoiseBandChart({ rows }) {
  const los = rows.map((r) => r.ci[0]);
  const his = rows.map((r) => r.ci[1]);
  const lo = Math.min(NOISE - 0.06, ...los) - 0.02;
  const hi = Math.max(NOISE + 0.06, ...his) + 0.02;
  const at = (v) => ((v - lo) / (hi - lo)) * 100;

  return (
    <div>
      <div className="eyebrow mb-3">Out-of-sample AUC with 95% bootstrap CI</div>
      <div className="relative">
        <div
          className="absolute top-0 bottom-5 border-l border-dashed border-ink-4 pointer-events-none"
          style={{ left: `${at(NOISE)}%` }}
        />
        <div className="space-y-3">
          {rows.map((r) => (
            <div key={r.key}>
              <div className="flex items-baseline justify-between gap-3 mb-1">
                <span className="text-2xs text-ink-3">{r.key}</span>
                <span className="num text-2xs font-semibold text-ink-2">
                  {r.auc} <span className="text-ink-4 font-normal">[{r.ci[0]}, {r.ci[1]}]</span>
                </span>
              </div>
              <div className="relative h-3">
                <div
                  className="absolute top-1 h-1 rounded-full bg-line-strong"
                  style={{ left: `${at(r.ci[0])}%`, width: `${at(r.ci[1]) - at(r.ci[0])}%` }}
                  title={`95% CI ${r.ci[0]} to ${r.ci[1]}`}
                />
                <div
                  className="absolute top-0 w-3 h-3 rounded-full bg-ink border-2 border-surface"
                  style={{ left: `calc(${at(r.auc)}% - 6px)` }}
                  title={`AUC ${r.auc}`}
                />
              </div>
            </div>
          ))}
        </div>
        <div className="relative h-4 mt-1">
          <span className="num absolute text-2xs text-ink-4" style={{ left: 0 }}>{lo.toFixed(2)}</span>
          <span
            className="num absolute text-2xs text-ink-3 font-medium -translate-x-1/2"
            style={{ left: `${at(NOISE)}%` }}
          >
            0.50 chance
          </span>
          <span className="num absolute text-2xs text-ink-4 right-0">{hi.toFixed(2)}</span>
        </div>
      </div>
    </div>
  );
}

export default function ModelReceipts({ report }) {
  if (!report) return null;
  const r = report.results;
  const act = r.activity_label || {};
  const pctf = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);

  const chartRows = [
    { key: "More big moves", auc: r.test_auc, ci: r.test_auc_ci95 },
    { key: "Direction", auc: r.directional_test_auc, ci: r.directional_test_auc_ci95 },
    { key: "Active period", auc: act.test_auc, ci: act.test_auc_ci95 },
  ].filter((x) => hasCi(x.ci) && x.auc != null);

  // "None beat noise" is only claimed when that is what the intervals say.
  const tested = chartRows.length || 3;
  const nulls = chartRows.filter((x) => includesNoise(x.ci)).length;
  const headline =
    chartRows.length === 0 || nulls === chartRows.length
      ? "I tested three predictive hypotheses. None beat noise."
      : `I tested ${tested} predictive hypotheses. Nothing clears the bar to ship.`;

  return (
    <section className="card p-4 md:p-5">
      <header className="mb-3">
        <h2 className="font-semibold text-ink">{headline}</h2>
        <p className="text-xs text-ink-3 mt-1">
          Pre-registered, out-of-sample, time-ordered split with an embargo.{" "}
          <span className="num">{report.n_symbols}</span> NSE stocks ·{" "}
          <span className="num">{report.date_range[0]} → {report.date_range[1]}</span> · real candles, never
          the simulator.
          {report.split?.ci_method && <> Intervals: {report.split.ci_method}.</>}
        </p>
      </header>

      {chartRows.length > 0 && (
        <div className="py-4 border-y border-line mb-1">
          <NoiseBandChart rows={chartRows} />
        </div>
      )}

      <div className={chartRows.length > 0 ? "" : "border-y border-line"}>
        <Verdict
          title="Does an unusual move predict MORE big moves over the next 3 days?"
          auc={r.test_auc}
          ci={r.test_auc_ci95}
          metrics={[
            ["base", pctf(r.test_base_rate)],
            ["Brier", r.test_brier],
            ["top-decile lift", `${r.top_decile_lift_vs_base}×`],
          ]}
        />
        <Verdict
          title="Does it predict the DIRECTION of the next move?"
          auc={r.directional_test_auc}
          ci={r.directional_test_auc_ci95}
          metrics={[["expected", "≈0.50"]]}
        />
        {act.test_auc != null && (
          <Verdict
            title="Does it predict an unusually ACTIVE period (volatility clustering)?"
            auc={act.test_auc}
            ci={act.test_auc_ci95}
            metrics={[
              ["base", pctf(act.test_base_rate)],
              ...(act.test_brier != null ? [["Brier", act.test_brier]] : []),
              ["top-decile lift", `${act.top_decile_lift_vs_base}×`],
            ]}
          />
        )}
      </div>

      <div className="rounded-lg bg-surface2 p-3.5 mt-4">
        <div className="eyebrow mb-1.5">What ships because of this</div>
        {report.ships_a_model === false && (
          <p className="text-xs text-ink-2 leading-relaxed">
            No learned model runs anywhere in this product. Not one card carries a predicted probability.
          </p>
        )}
        <p className="text-xs text-ink-2 leading-relaxed mt-2">
          The ranking you see on the watchlist is <span className="font-medium text-ink">descriptive</span>{" "}
          — how unusual a move was in this stock's own volatility, market-adjusted, plus relative volume and
          52-week breaks, on transparent 1:1:1 weights. It tells you what <em>happened</em>. It never claims to
          know what happens next, and no card carries a predicted probability.
        </p>
      </div>

      <p className="text-2xs text-ink-4 mt-3 leading-relaxed">
        <span className="num">{report.n_train + report.n_test}</span> symbol-days, but overlapping windows and
        correlated large-caps make the effective sample far smaller — which is why the intervals are this wide,
        and why they're on screen rather than a single flattering number.
      </p>
    </section>
  );
}
