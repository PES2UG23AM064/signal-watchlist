import { inr, pct, displaySymbol } from "../../format.js";
import { ProvenanceChips } from "../Badges.jsx";

// Explainability: every number behind the score, as a plain two-column table. Nothing is a black box,
// and nothing here is a prediction. Lives in the drawer so the feed itself stays dense.

function Metric({ label, value, note, tone = "default" }) {
  const tones = { default: "text-ink", up: "text-up", down: "text-down" };
  return (
    <div className="flex items-baseline justify-between gap-4 py-2 border-b border-line last:border-0">
      <dt className="text-xs text-ink-3 shrink-0">{label}</dt>
      <dd className="text-right min-w-0">
        <span className={`num text-sm font-semibold ${tones[tone]}`}>{value}</span>
        {note && <span className="block text-2xs text-ink-4 mt-0.5">{note}</span>}
      </dd>
    </div>
  );
}

// The endpoint can lie: a stock that ran +3% and came back to flat still HAPPENED. Draw the range.
function PathTrack({ trough, peak, now }) {
  const lo = Math.min(trough, peak, now, 0);
  const hi = Math.max(trough, peak, now, 0);
  const span = hi - lo || 1;
  const at = (v) => ((v - lo) / span) * 100;
  return (
    <div className="pt-3">
      <div className="relative h-9">
        <div className="absolute top-3 left-0 right-0 h-1 rounded-full bg-surface2" />
        <div
          className="absolute top-3 h-1 rounded-full bg-line-strong"
          style={{ left: `${at(Math.min(trough, peak))}%`, width: `${Math.abs(at(peak) - at(trough))}%` }}
        />
        <div className="absolute top-1.5 w-1 h-4 rounded-full bg-ink" style={{ left: `calc(${at(now)}% - 2px)` }} title="where it ended up" />
        <span className="num absolute top-5 text-2xs text-ink-4" style={{ left: 0 }}>{pct(Math.min(trough, peak))}</span>
        <span className="num absolute top-5 text-2xs text-ink-4 right-0">{pct(Math.max(trough, peak))}</span>
      </div>
    </div>
  );
}

export default function ExplainPanel({ row }) {
  const e = row.signal.explain;
  const ch = row.change_since_seen;
  const up = ch.direction === "up";
  const mins = Math.round(e.elapsed_seconds / 60);

  return (
    <div className="space-y-5">
      {/* Where it stands now */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <div className="num text-2xl font-semibold text-ink">{inr(row.price)}</div>
          <div className="num text-xs text-ink-4 mt-0.5">you last saw {inr(row.last_seen.price)}</div>
        </div>
        <div className={`num text-lg font-semibold ${up ? "text-up" : "text-down"}`}>{pct(ch.pct)}</div>
      </div>

      <ProvenanceChips provenance={row.provenance} />

      {row.signal.reasons.length > 0 && (
        <div>
          <div className="eyebrow mb-1.5">Why it surfaced</div>
          <div className="flex flex-wrap gap-1.5">
            {row.signal.reasons.map((r) => (
              <span key={r} className="chip chip-delayed">{r}</span>
            ))}
          </div>
        </div>
      )}

      <div>
        <div className="eyebrow mb-1">The numbers, in plain words</div>
        <dl>
          <Metric
            label="It moved"
            value={pct(e.move_pct)}
            tone={e.move_pct >= 0 ? "up" : "down"}
            note={`since you last looked, ${mins} minute${mins === 1 ? "" : "s"} ago`}
          />
          <Metric
            label="…and on its own"
            value={pct(e.market_adjusted_pct)}
            tone={e.market_adjusted_pct >= 0 ? "up" : "down"}
            note={
              e.beta != null
                ? "the market moved too — this is the part that was just this stock"
                : "we had no index reading to compare against"
            }
          />
          <Metric
            label="How unusual that is"
            value={`${e.sigma_move.toFixed(1)}×`}
            note={`this stock normally moves about ${e.sigma_daily_pct}% in a full day — this is ${e.sigma_move.toFixed(
              1
            )} times what you'd expect in ${mins} minutes`}
          />
          <Metric
            label="Trading activity"
            value={`${e.vol_ratio.toFixed(1)}×`}
            note={
              e.vol_ratio >= 1.5
                ? "busier than usual — more people are trading it than normal"
                : "about as busy as a normal day"
            }
          />
          <Metric
            label="52-week high/low"
            value={e.crossed ? `broke its ${e.crossed}` : "not broken"}
            note={
              e.crossed
                ? `this is a price it hasn't reached in a year`
                : "it stayed inside its range for the year"
            }
          />
          {row.peer_residual_z != null && (
            <Metric
              label="Versus its usual peers"
              value={`${Math.abs(row.peer_residual_z).toFixed(1)}×`}
              note={`it moved ${
                row.peer_residual_z >= 0 ? "further up" : "further down"
              } than the stocks it normally travels with — so this looks like company news, not the sector`}
            />
          )}
          {e.peak_pct != null && e.trough_pct != null && (
            <Metric
              label="How it got here"
              value={`${pct(e.trough_pct)} … ${pct(e.peak_pct)}`}
              note={
                e.path_note ||
                "the low and high it touched while you were away — the final number isn't the whole story"
              }
            />
          )}
        </dl>
        {e.peak_pct != null && e.trough_pct != null && (
          <PathTrack trough={e.trough_pct} peak={e.peak_pct} now={ch.pct} />
        )}
      </div>

      {row.impact_inr != null && (
        <div className="rounded-lg bg-surface2 px-3 py-2.5 flex items-center justify-between">
          <span className="text-xs text-ink-3">
            On your <span className="num">{row.quantity}</span> shares
          </span>
          <span className={`num text-sm font-semibold ${row.impact_inr >= 0 ? "text-up" : "text-down"}`}>
            {row.impact_inr >= 0 ? "+" : "−"}{inr(Math.abs(row.impact_inr))}
          </span>
        </div>
      )}

      <p className="text-2xs text-ink-4 leading-relaxed">
        This describes what already happened. It is not a forecast, and nothing here is advice to buy or sell{" "}
        {displaySymbol(row.symbol)}. The thresholds are fixed and published in Insights.
      </p>
    </div>
  );
}
