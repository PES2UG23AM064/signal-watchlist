import { FlaskConical, HelpCircle, LogOut, Moon, Sun } from "lucide-react";
import { MarketPill } from "../Badges.jsx";

const TABS = [
  { id: "watchlist", label: "Watchlist" },
  { id: "how", label: "How it works" },
];

function Tabs({ tab, onTab, className = "" }) {
  return (
    <div role="tablist" aria-label="Sections" className={`flex gap-0.5 p-0.5 rounded-lg bg-surface2 ${className}`}>
      {TABS.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={tab === t.id}
          onClick={() => onTab(t.id)}
          className={`grow md:grow-0 text-xs font-medium px-3 py-1.5 rounded-[6px] transition-colors duration-150 ${
            tab === t.id ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// "Asha" from a display name, else the part of the email before the @ — never the raw email.
function greet(account) {
  if (!account) return "";
  if (account.display_name) return account.display_name;
  const local = String(account.email || "").split("@")[0];
  return local ? local.charAt(0).toUpperCase() + local.slice(1) : "";
}

export default function TopBar({
  tab, onTab, market, account, theme, onToggleTheme, showDemo, onDemo, onLegend, onLogout, live,
}) {
  const name = greet(account);
  return (
    <header className="sticky top-0 z-30 bg-app/85 backdrop-blur-md border-b border-line">
      <div className="mx-auto max-w-[1400px] px-4 lg:px-6">
        <div className="h-14 flex items-center gap-3">
          <div className="flex items-center gap-2 shrink-0">
            <span className="relative flex w-2.5 h-2.5">
              {live && <span className="absolute inset-0 rounded-full bg-brand animate-ping opacity-60" />}
              <span className="relative w-2.5 h-2.5 rounded-full bg-brand" />
            </span>
            <span className="font-semibold tracking-tight">Signal</span>
          </div>

          <Tabs tab={tab} onTab={onTab} className="hidden md:flex ml-4" />

          <div className="grow" />

          <MarketPill market={market} />

          <button onClick={onLegend} className="btn btn-ghost btn-sm" title="What do the labels on each price mean?">
            <HelpCircle size={15} />
            <span className="hidden lg:inline">Labels</span>
          </button>

          <button
            onClick={onToggleTheme}
            className="btn btn-ghost btn-sm"
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>

          {showDemo && (
            <button onClick={onDemo} className="btn btn-outline btn-sm" title="Demo tools: time travel and fault injection">
              <FlaskConical size={15} />
              <span className="hidden sm:inline">Demo</span>
            </button>
          )}

          <button onClick={onLogout} className="btn btn-ghost btn-sm" title={name ? `Signed in as ${name} — log out` : "Log out"}>
            <LogOut size={15} />
            <span className="hidden lg:inline max-w-[9rem] truncate">{name || "Log out"}</span>
          </button>
        </div>

        <Tabs tab={tab} onTab={onTab} className="md:hidden mb-2.5" />
      </div>
    </header>
  );
}
