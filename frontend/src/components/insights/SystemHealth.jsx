import { displaySymbol } from "../../format.js";

// Observability in the product: what's serving the numbers, how fresh they are, what got refused.

function Cell({ label, value, sub }) {
  return (
    <div className="rounded-lg bg-surface2 p-3 min-w-0">
      <div className="eyebrow truncate">{label}</div>
      <div className="num text-sm font-semibold text-ink mt-1 truncate" title={String(value)}>{value}</div>
      <div className="num text-2xs text-ink-4 mt-0.5 truncate" title={sub}>{sub}</div>
    </div>
  );
}

const FRESH_BAR = { fresh: "bg-fresh", delayed: "bg-delayed", stale: "bg-stale" };

export default function SystemHealth({ status }) {
  if (!status) return null;
  const p = status.provider;
  const pl = status.poller;
  const d = status.data;
  const lag = pl.last_poll_at ? Math.round((Date.now() - new Date(pl.last_poll_at).getTime()) / 1000) : null;
  const healthy = pl.enabled && lag != null && lag < 30 && d.quarantined_last_hour < 50;
  const maxAge = Math.max(30, ...d.freshness.map((f) => f.age_seconds));
  // Freshness covers every watched symbol in the deployment; show the oldest and count the rest.
  const RANK = { fresh: 0, delayed: 1, stale: 2, no_data: 3 };
  const ranked = [...d.freshness].sort(
    (a, b) => (RANK[b.freshness] ?? 0) - (RANK[a.freshness] ?? 0) || b.age_seconds - a.age_seconds
  );
  const shown = ranked.slice(0, 12);
  const hidden = ranked.length - shown.length;

  return (
    <section className="card p-4 md:p-5">
      <header className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h2 className="font-semibold text-ink flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${healthy ? "bg-fresh" : "bg-delayed"}`} />
            System health
          </h2>
          <p className="text-xs text-ink-3 mt-1">
            {p.mode === "composite"
              ? `Live feed ${p.serving_live ? "serving" : "on fallback"} · breaker ${p.breaker}`
              : `Source: ${p.mode}`}
            {lag != null && <> · last poll <span className="num">{lag}s</span> ago</>}
          </p>
        </div>
      </header>

      <div className="lg:grid lg:grid-cols-3 lg:gap-6 lg:items-start">
        <div>
          <div className="grid grid-cols-3 lg:grid-cols-1 gap-2">
            <Cell
              label="Provider"
              value={p.mode}
              sub={
                p.mode === "composite"
                  ? `${p.primary} → ${p.fallback} · ${p.last_route}`
                  : p.mode === "replay"
                  ? "deterministic, real-anchored"
                  : "live"
              }
            />
            <Cell
              label="Poll cycle"
              value={pl.last_cycle_seconds != null ? `${pl.last_cycle_seconds}s` : "—"}
              sub={`every ${pl.interval_seconds}s · ${pl.last_symbols} symbols · ${pl.errors} errors`}
            />
            <Cell
              label="Refused"
              value={d.quarantined_last_hour}
              sub={`in 1h · ${d.quote_rows.toLocaleString()} rows stored`}
            />
          </div>

          {pl.paused_symbols?.length > 0 && (
            <div className="text-xs text-delayed bg-delayed/10 rounded-lg px-3 py-2 mt-3">
              Simulated outage: {pl.paused_symbols.map(displaySymbol).join(", ")} — feed paused, data aging
              honestly.
            </div>
          )}
        </div>

        <div className="mt-4 lg:mt-0 lg:col-span-2">
          <div className="eyebrow mb-2">
            Data age per symbol · oldest first
            {hidden > 0 && <span className="text-ink-4 normal-case tracking-normal"> · {hidden} more all fresh</span>}
          </div>
          <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1.5">
            {shown.map((f) => (
              <div key={f.symbol} className="flex items-center gap-2.5">
                <span className="text-xs text-ink-2 w-20 shrink-0 truncate">{displaySymbol(f.symbol)}</span>
                <div className="h-1.5 grow rounded-full bg-surface2 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${FRESH_BAR[f.freshness] || "bg-line-strong"}`}
                    style={{ width: `${Math.max(3, Math.min(100, (f.age_seconds / maxAge) * 100))}%` }}
                  />
                </div>
                <span className="num text-2xs text-ink-3 w-12 text-right shrink-0">{f.age_seconds}s</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <p className="text-2xs text-ink-4 mt-4 leading-relaxed">
        {status.market.label} — {status.market.detail}. If this panel and the badges on the watchlist ever
        disagree, the badges are wrong; that's the point of having both. Leader election:{" "}
        {pl.leader_election}.
      </p>
    </section>
  );
}
