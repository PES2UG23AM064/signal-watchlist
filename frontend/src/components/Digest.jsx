import { inr, pct, displaySymbol } from "../format.js";

export default function Digest({ changes, onMarkAllSeen }) {
  const meaningful = changes.filter((c) => c.is_meaningful);
  const count = meaningful.length;

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

  return (
    <section className="mb-6">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide">While you were away</h2>
        <button onClick={onMarkAllSeen} className="text-xs text-brand font-medium hover:underline">
          Mark all seen
        </button>
      </div>

      {count > 0 && (
        <p className="text-sm text-slate-600 mb-3">
          <span className="font-semibold text-slate-900">{count}</span>{" "}
          {count === 1 ? "thing needs" : "things need"} your attention
        </p>
      )}

      <div className="space-y-2.5">
        {changes.map((c) => {
          const up = c.change_since_seen.direction === "up";
          return (
            <div
              key={c.symbol}
              className={`bg-white rounded-2xl border p-4 ${
                c.is_meaningful ? "border-slate-200 shadow-sm" : "border-slate-100 opacity-80"
              }`}
            >
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-semibold text-slate-900">{displaySymbol(c.symbol)}</div>
                  <div className="text-sm text-slate-500 mt-0.5">{c.reason}</div>
                </div>
                <div className="text-right">
                  <div className="font-semibold text-slate-900">{inr(c.price)}</div>
                  <div className={`text-sm font-medium ${up ? "text-up" : "text-down"}`}>
                    {pct(c.change_since_seen.pct)}
                  </div>
                </div>
              </div>
              <div className="text-xs text-slate-400 mt-2">
                you last saw {inr(c.last_seen.price)}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
