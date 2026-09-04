import { useEffect, useRef, useState } from "react";
import { AlertCircle, X } from "lucide-react";

// The handful of shapes used in more than one place. Anything used once stays inline where it's used.

// A labelled number. The workhorse of the summary strip and the Insights tab.
export function Stat({ label, value, sub, tone = "default", title }) {
  const tones = { default: "text-ink", up: "text-up", down: "text-down", muted: "text-ink-3" };
  return (
    <div className="min-w-0" title={title}>
      <div className="eyebrow truncate">{label}</div>
      <div className={`num text-base md:text-xl font-semibold mt-0.5 truncate ${tones[tone]}`}>{value}</div>
      {sub && <div className="text-2xs text-ink-4 mt-0.5 truncate">{sub}</div>}
    </div>
  );
}

// Horizontal proportion bar — used for correlations, alert counts and calibration in Insights.
export function Bar({ value, max = 1, color = "bg-brand", className = "" }) {
  const w = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className={`h-1.5 rounded-full bg-surface2 overflow-hidden ${className}`}>
      <div className={`h-full rounded-full ${color}`} style={{ width: `${w}%` }} />
    </div>
  );
}

// Error surface: transient, out of the layout, dismissible. Replaces the old inline red block that
// pushed content down every time a poll hiccuped.
export function Toast({ message, onDismiss }) {
  // The timer keys on the MESSAGE only. `onDismiss` is a fresh closure on every render, and the app
  // re-renders on every poll — keyed on it, the 6s timer restarted every 5s and never fired.
  const dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;
  useEffect(() => {
    if (!message) return;
    const id = setTimeout(() => dismiss.current(), 6000);
    return () => clearTimeout(id);
  }, [message]);
  if (!message) return null;
  return (
    <div
      role="status"
      className="fixed z-[60] bottom-4 left-1/2 -translate-x-1/2 md:left-auto md:right-5 md:translate-x-0
                 flex items-start gap-2.5 max-w-[calc(100vw-2rem)] md:max-w-sm
                 bg-ink text-app text-sm rounded-xl px-3.5 py-2.5 shadow-lift animate-fade-in"
    >
      <AlertCircle size={16} className="shrink-0 mt-0.5 text-down" />
      <span className="min-w-0">{message}</span>
      <button onClick={onDismiss} aria-label="Dismiss" className="shrink-0 -mr-1 text-ink-4 hover:text-white">
        <X size={15} />
      </button>
    </div>
  );
}

// First-paint placeholder, so the app never flashes an empty "all caught up" it will contradict.
export function SkeletonCard({ lines = 2 }) {
  return (
    <div className="card p-4">
      <div className="flex justify-between gap-4">
        <div className="skeleton h-4 w-24" />
        <div className="skeleton h-4 w-16" />
      </div>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton h-3 mt-3" style={{ width: `${80 - i * 25}%` }} />
      ))}
    </div>
  );
}

// A collapsible section header used across the Insights tab.
export function Disclosure({ title, subtitle, icon: Icon, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="card overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="w-full text-left px-4 py-3.5 flex items-center gap-3 hover:bg-surface2 transition-colors"
      >
        {Icon && <Icon size={18} className="text-ink-4 shrink-0" />}
        <div className="min-w-0 grow">
          <div className="font-semibold text-ink text-sm">{title}</div>
          {subtitle && <div className="text-xs text-ink-3 mt-0.5">{subtitle}</div>}
        </div>
        <span className="text-ink-4 text-xs shrink-0">{open ? "Hide" : "Show"}</span>
      </button>
      {open && <div className="px-4 pb-4 pt-1 border-t border-line">{children}</div>}
    </section>
  );
}
