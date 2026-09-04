import { useEffect, useState } from "react";
import { api } from "../api.js";
import { displaySymbol } from "../format.js";

// Observability, in the product: what's serving the numbers, how fresh they are, what got refused.
// Shown collapsed; refreshes every 10s while open.
export default function StatusPanel() {
  const [s, setS] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => api.getStatus().then((d) => alive && setS(d)).catch(() => {});
    load();
    const id = setInterval(load, open ? 10000 : 30000);
    return () => { alive = false; clearInterval(id); };
  }, [open]);

  if (!s) return null;
  const p = s.provider, pl = s.poller, d = s.data;
  const lag = pl.last_poll_at ? Math.round((Date.now() - new Date(pl.last_poll_at).getTime()) / 1000) : null;
  const worst = d.freshness.reduce((w, f) => (f.age_seconds > (w?.age_seconds ?? -1) ? f : w), null);
  const dot = (ok) => <span className={`inline-block w-1.5 h-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`} />;

  return (
    <section className="mt-4">
      <button onClick={() => setOpen(!open)} className="w-full text-left bg-white rounded-2xl border border-slate-100 p-4 hover:border-slate-200">
        <div className="flex items-center justify-between">
          <div>
            <div className="font-semibold text-slate-900 flex items-center gap-2">
              {dot(pl.enabled && lag != null && lag < 30 && d.quarantined_last_hour < 50)} System health
            </div>
            <div className="text-xs text-slate-500 mt-0.5">
              {p.mode === "composite" ? `live feed: ${p.serving_live ? "serving" : "fallback"} · breaker ${p.breaker}` : `source: ${p.mode}`}
              {lag != null && ` · last poll ${lag}s ago`} · {d.quarantined_last_hour} refused (1h)
            </div>
          </div>
          <span className="text-slate-400 text-sm">{open ? "▲" : "▼"}</span>
        </div>
      </button>

      {open && (
        <div className="bg-white rounded-2xl border border-slate-100 p-4 mt-2 text-xs space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="provider" value={p.mode} sub={p.mode === "composite" ? `${p.primary} → ${p.fallback} · ${p.last_route}` : (p.mode === "replay" ? "deterministic, real-anchored" : "live")} />
            <Stat label="poll cycle" value={pl.last_cycle_seconds != null ? `${pl.last_cycle_seconds}s` : "—"} sub={`every ${pl.interval_seconds}s · ${pl.last_symbols} symbols · ${pl.errors} errors`} />
            <Stat label="data" value={`${d.watched_symbols} symbols`} sub={`${d.quote_rows.toLocaleString()} rows · ${d.quarantined_last_hour} quarantined/1h`} />
          </div>
          {pl.paused_symbols?.length > 0 && (
            <div className="text-amber-700 bg-amber-50 rounded-md px-2 py-1.5">
              Simulated outage: {pl.paused_symbols.map(displaySymbol).join(", ")} — feed paused, data aging honestly.
            </div>
          )}
          <div>
            <div className="text-slate-500 mb-1">Freshness per symbol {worst && <span className="text-slate-400">· oldest {displaySymbol(worst.symbol)} {worst.age_seconds}s</span>}</div>
            <div className="flex flex-wrap gap-1">
              {d.freshness.map((f) => (
                <span key={f.symbol} className={`px-1.5 py-0.5 rounded ${f.freshness === "fresh" ? "bg-emerald-50 text-emerald-700" : f.freshness === "delayed" ? "bg-amber-50 text-amber-700" : "bg-red-50 text-red-700"}`}>
                  {displaySymbol(f.symbol)} {f.age_seconds}s
                </span>
              ))}
            </div>
          </div>
          <p className="text-slate-400">
            Market: {s.market.label} — {s.market.detail}. If this panel and the badges ever disagree, the badges are wrong; that's the point of having both.
          </p>
        </div>
      )}
    </section>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="bg-slate-50 rounded-lg p-2">
      <div className="text-slate-500">{label}</div>
      <div className="text-slate-900 font-semibold mt-0.5 truncate" title={String(value)}>{value}</div>
      <div className="text-slate-400 truncate" title={sub}>{sub}</div>
    </div>
  );
}
