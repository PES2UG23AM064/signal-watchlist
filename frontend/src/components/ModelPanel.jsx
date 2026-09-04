import { useEffect, useState } from "react";
import { api } from "../api.js";

// "Receipts": how Signal knows what's meaningful. Shows ALL three pre-registered backtest results on
// real NSE candles — including the two that found no edge. Honesty is the feature.
export default function ModelPanel() {
  const [report, setReport] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api.getModel().then(setReport).catch(() => setReport(null));
  }, []);

  if (!report) return null;
  const r = report.results;
  const act = r.activity_label;
  const pctf = (x) => `${Math.round(x * 100)}%`;

  return (
    <section className="mt-8">
      <button
        onClick={() => setOpen(!open)}
        className="w-full text-left bg-white rounded-2xl border border-slate-100 p-4 hover:border-slate-200"
      >
        <div className="flex items-center justify-between">
          <div>
            <div className="font-semibold text-slate-900">How Signal decides what's "meaningful"</div>
            <div className="text-xs text-slate-500 mt-0.5">
              Backtested on {report.n_symbols} NSE stocks · {report.date_range[0]} → {report.date_range[1]} · real candles, not the simulator
            </div>
          </div>
          <span className="text-slate-400 text-sm">{open ? "▲" : "▼"}</span>
        </div>
      </button>

      {open && (
        <div className="bg-white rounded-2xl border border-slate-100 p-4 mt-2 text-sm space-y-4">
          <p className="text-slate-600">
            The ranking is <span className="font-medium text-slate-900">descriptive</span>: how unusual a move was
            in this stock's own volatility, market-adjusted, plus unusual volume and 52-week breaks. We tested
            whether that <em>predicts</em> anything. Three pre-registered questions, out-of-sample, time-split:
          </p>

          <div className="space-y-2">
            <Row
              title="Does an unusual move predict MORE big moves over the next 3 days?"
              verdict="No edge"
              bad
              detail={`AUC ${r.test_auc} · base rate ${pctf(r.test_base_rate)} · top-decile lift ${r.top_decile_lift_vs_base}×`}
            />
            <Row
              title="Does it predict the DIRECTION of the next move?"
              verdict="No edge"
              bad
              detail={`AUC ${r.directional_test_auc} — an efficient market should give you nothing here, and it did. This app never tells you what to buy.`}
            />
            <Row
              title="Does it predict an unusually ACTIVE period (volatility clustering)?"
              verdict="Weak, real"
              detail={`AUC ${act.test_auc} · base rate ${pctf(act.test_base_rate)} · top-decile lift ${act.top_decile_lift_vs_base}× — driven almost entirely by relative volume`}
            />
          </div>

          <div className="text-xs text-slate-500 bg-slate-50 rounded-lg p-3 space-y-1">
            <div>
              <span className="font-medium text-slate-700">What ships:</span> the ranking stays descriptive with
              transparent 1:1:1 weights (no learned "predictive" weights — the data doesn't support them). The one
              learned signal is the <span className="font-medium">activity outlook</span> tag, a 3-coefficient
              logistic regression:
              {" "}
              {report.shipped_model &&
                report.shipped_model.features.map((f, i) => (
                  <span key={f} className="font-mono">
                    {f} {report.shipped_model.coef[i] >= 0 ? "+" : ""}{report.shipped_model.coef[i].toFixed(2)}{i < 2 ? ", " : ""}
                  </span>
                ))}
              .
            </div>
            <div>
              <span className="font-medium text-slate-700">Sample honesty:</span> {report.n_train + report.n_test} symbol-days,
              but overlapping label windows and correlated large-caps put the effective sample in the low hundreds —
              which is why this is 3 features, not 30.
            </div>
          </div>

          {report.cohorts && <CohortSection c={report.cohorts} />}
        </div>
      )}
    </section>
  );
}

// Where ML demonstrably works here: unsupervised co-movement cohorts. Structure + stability + a
// counterfactual alert replay — counts and correlations, so none of it can come back "null".
function CohortSection({ c }) {
  const s = c.structure, st = c.stability, a = c.alert_counterfactual;
  const multi = (s.cohorts || []).filter((g) => g.length > 1);
  return (
    <div className="pt-3 border-t border-slate-100 space-y-3">
      <div>
        <div className="font-medium text-slate-900">Where ML does work: who moves with whom</div>
        <p className="text-slate-600 text-sm mt-1">
          We gave the model a year of daily <em>returns</em> and nothing else — no sector labels. It groups the
          symbols that move as one, so a pack move folds into a single card and the stock moving <em>alone</em>
          is promoted. Grouping never hides a symbol.
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {multi.map((g, i) => (
          <span key={i} className="text-[11px] px-2 py-1 rounded-full bg-violet-50 text-violet-700">
            {g.map((x) => x.replace(/\.NS$/, "")).join(" · ")}
          </span>
        ))}
        {(s.cohorts || []).filter((g) => g.length === 1).length > 0 && (
          <span className="text-[11px] px-2 py-1 rounded-full bg-slate-100 text-slate-500">
            {(s.cohorts || []).filter((g) => g.length === 1).length} move on their own
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <Stat label="within-cohort corr" value={s.mean_intra_cohort_corr} sub={`vs ${s.mean_inter_cohort_corr} across`} />
        <Stat label="out-of-sample stability" value={`${st.symbols_kept_grouping}`} sub={`ARI ${st.adjusted_rand_index} · half-year vs half-year`} />
        <Stat label="alerts over the year" value={`${a.cards_old_rule} → ${a.cards_cohort_rule}`} sub={`${a.reduction_pct}% fewer · ${a.moving_alone_hidden_in_a_group} hidden`} />
      </div>
      <p className="text-[11px] text-slate-400">
        Honest read: the sector structure is strong; half-year stability is modest (thin windows flip borderline pairs);
        the alert reduction is small on a 9-stock set and grows with more correlated names. {a.moving_alone_flags} "moving
        alone" flags, none ever folded into a group — that's a test, not a claim.
      </p>
    </div>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="bg-slate-50 rounded-lg p-2">
      <div className="text-slate-500">{label}</div>
      <div className="text-slate-900 font-semibold mt-0.5">{value}</div>
      <div className="text-slate-400">{sub}</div>
    </div>
  );
}

function Row({ title, verdict, detail, bad }) {
  return (
    <div className="flex gap-3 items-start">
      <span
        className={`shrink-0 mt-0.5 text-[11px] px-1.5 py-0.5 rounded font-medium ${
          bad ? "bg-slate-100 text-slate-600" : "bg-emerald-50 text-emerald-700"
        }`}
      >
        {verdict}
      </span>
      <div>
        <div className="text-slate-800">{title}</div>
        <div className="text-xs text-slate-500 mt-0.5">{detail}</div>
      </div>
    </div>
  );
}
