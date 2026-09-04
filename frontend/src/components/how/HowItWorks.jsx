import { useEffect, useState } from "react";
import { Activity, ChevronDown, Layers, ShieldCheck } from "lucide-react";
import { api } from "../../api.js";
import { displaySymbol, timeAgo } from "../../format.js";
import CohortLab from "../insights/CohortLab.jsx";
import ModelReceipts from "../insights/ModelReceipts.jsx";
import SystemHealth from "../insights/SystemHealth.jsx";

// Customer-first. Three short answers to the three questions a real user actually has, then one
// collapsed expander holding every piece of evidence for the people who want to audit us.

function Section({ icon: Icon, title, children }) {
  return (
    <section className="card p-5">
      <div className="flex items-center gap-2.5 mb-3">
        <span className="w-8 h-8 rounded-lg bg-brand/10 text-brand-dark dark:text-brand flex items-center justify-center shrink-0">
          <Icon size={16} />
        </span>
        <h2 className="font-semibold text-ink">{title}</h2>
      </div>
      {children}
    </section>
  );
}

export default function HowItWorks({ cohorts, items, market }) {
  const [report, setReport] = useState(null);
  const [status, setStatus] = useState(null);
  const [openEvidence, setOpenEvidence] = useState(false);

  // The evidence endpoints are only fetched once someone opens the expander.
  useEffect(() => {
    if (!openEvidence) return;
    let alive = true;
    api.getModel().then((d) => alive && setReport(d)).catch(() => {});
    const load = () => api.getStatus().then((d) => alive && setStatus(d)).catch(() => {});
    load();
    const id = setInterval(load, 10000);
    return () => { alive = false; clearInterval(id); };
  }, [openEvidence]);

  const userGroups = (cohorts || []).filter((c) => c.members.length > 1);
  const simulated = items.some((i) => i.provenance?.is_simulated);
  const oldest = items.reduce(
    (w, i) => (i.provenance && (!w || i.provenance.age_seconds > w.age_seconds) ? i.provenance : w),
    null
  );

  return (
    <div className="max-w-3xl">
      <h1 className="text-lg font-semibold text-ink tracking-tight">How it works</h1>
      <p className="text-sm text-ink-3 mt-1 mb-6">
        Three things worth knowing about the numbers you're looking at.
      </p>

      <div className="space-y-4">
        <Section icon={Activity} title="How Signal decides what's worth your attention">
          <p className="text-sm text-ink-2 leading-relaxed">
            A 1% move means something very different for a calm stock than for a jumpy one, so we compare
            each move against what that stock normally does in a day — after taking out whatever the whole
            market did that day. We also flag a new 52-week high or low, and moves that spiked and came back
            while you were away. Unusually heavy trading is noted on the card, but on its own it never
            puts a stock in front of you — only what happened since you looked does.
          </p>
          <p className="text-sm text-ink-2 leading-relaxed mt-3">
            Everything else stays quiet. Tap <span className="font-medium text-ink">Why?</span> on any card
            to see the exact numbers behind it.
          </p>
          <p className="text-sm font-medium text-ink mt-4 pt-3 border-t border-line">
            It tells you what happened. It never predicts what happens next.
          </p>
        </Section>

        <Section icon={Layers} title="Stocks that move together">
          {userGroups.length > 0 ? (
            <>
              <div className="flex flex-wrap gap-1.5 mb-3">
                {userGroups.map((c) => (
                  <span key={c.id} className="chip chip-cohort">
                    {c.members.map(displaySymbol).join(" · ")}
                  </span>
                ))}
              </div>
              <p className="text-sm text-ink-2 leading-relaxed">
                These stocks in your list normally rise and fall together. When they all move, that's the
                sector — so you get one card instead of several. When one of them breaks away from the rest,
                that's usually news about that company, and we push it to the top.
              </p>
            </>
          ) : (
            <p className="text-sm text-ink-2 leading-relaxed">
              None of the stocks on your list move closely enough together to be grouped yet. When they do,
              a sector-wide move becomes a single card instead of several — and any stock breaking away from
              its group gets pushed to the top.
            </p>
          )}
        </Section>

        <Section icon={ShieldCheck} title="Where the data comes from and how fresh it is">
          <dl className="divide-y divide-line">
            <div className="flex items-baseline justify-between gap-4 py-2.5">
              <dt className="text-sm text-ink-3">Source</dt>
              <dd className="text-sm font-medium text-ink text-right">
                {simulated ? "Simulated replay of real market data" : "Live market feed"}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-4 py-2.5">
              <dt className="text-sm text-ink-3">Oldest price on your list</dt>
              <dd className="num text-sm font-medium text-ink text-right">
                {oldest ? timeAgo(oldest.event_time) : "—"}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-4 py-2.5">
              <dt className="text-sm text-ink-3">Market</dt>
              <dd className="text-sm font-medium text-ink text-right">{market?.label || "—"}</dd>
            </div>
          </dl>
          <p className="text-sm text-ink-2 leading-relaxed mt-3">
            Every price on your watchlist carries its own age. If a price stops arriving we show you how old
            it is rather than quietly leaving a stale number on screen, and prices that fail our checks are
            thrown away instead of shown.
          </p>
        </Section>

        {/* The evidence layer. Collapsed by default — present for anyone who wants to audit the claims. */}
        <section className="card overflow-hidden">
          <button
            onClick={() => setOpenEvidence(!openEvidence)}
            aria-expanded={openEvidence}
            className="w-full text-left px-5 py-4 flex items-center gap-3 hover:bg-surface2 transition-colors duration-150"
          >
            <div className="min-w-0 grow">
              <div className="font-semibold text-ink text-sm">See the evidence</div>
              <div className="text-xs text-ink-3 mt-0.5">
                For the technically curious: what we tested, what failed, and what the system is doing now.
              </div>
            </div>
            <ChevronDown
              size={18}
              className={`text-ink-4 shrink-0 transition-transform duration-200 ${openEvidence ? "rotate-180" : ""}`}
            />
          </button>

          {openEvidence && (
            <div className="border-t border-line p-4 md:p-5 space-y-5 bg-surface2/40">
              <ModelReceipts report={report} />
              <CohortLab report={report?.cohorts} userCohorts={cohorts} />
              <SystemHealth status={status} />
              {!report && !status && (
                <p className="text-sm text-ink-3 text-center py-6">
                  Loading the evidence…
                </p>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
