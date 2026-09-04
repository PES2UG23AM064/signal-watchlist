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
        </div>
      )}
    </section>
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
