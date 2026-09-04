import { useCallback, useEffect, useState } from "react";
import { api, getToken, API_BASE } from "./api.js";
import AuthScreen from "./components/auth/AuthScreen.jsx";
import Legend from "./components/Legend.jsx";
import TopBar from "./components/layout/TopBar.jsx";
import SummaryStrip from "./components/layout/SummaryStrip.jsx";
import DigestFeed from "./components/digest/DigestFeed.jsx";
import ExplainPanel from "./components/digest/ExplainPanel.jsx";
import WatchlistPanel from "./components/watchlist/WatchlistPanel.jsx";
import HowItWorks from "./components/how/HowItWorks.jsx";
import DemoDrawer from "./components/demo/DemoDrawer.jsx";
import Drawer from "./components/ui/Drawer.jsx";
import { Toast } from "./components/ui/Bits.jsx";
import { displaySymbol } from "./format.js";
import { useTheme } from "./theme.js";

// Transport: Server-Sent Events push a "quotes updated" tick after every poll cycle, and we refetch
// /state on each tick. Polling stays as the FALLBACK (slower once SSE is connected) — same refresh seam.
const POLL_MS = 5000;
const POLL_MS_WITH_SSE = 30000;

export default function App() {
  // "checking" while we validate a remembered token against /auth/me — we never render a dashboard
  // against a token the server hasn't confirmed.
  const [authState, setAuthState] = useState(getToken() ? "checking" : "out");
  const [account, setAccount] = useState(null);
  const [theme, toggleTheme] = useTheme();

  const [items, setItems] = useState([]);
  const [changes, setChanges] = useState([]);
  const [cohorts, setCohorts] = useState([]);
  const [market, setMarket] = useState(null);
  const [error, setError] = useState(null);
  const [loaded, setLoaded] = useState(false);

  const [tab, setTab] = useState("watchlist");
  // The drawer holds a SYMBOL, never a row object: rows are re-derived from the live arrays on every
  // render, so an open panel keeps updating with the 5s refresh and can't show a row that's gone.
  const [explainSymbol, setExplainSymbol] = useState(null);
  const [demoOpen, setDemoOpen] = useState(false);
  const [legendOpen, setLegendOpen] = useState(false);
  const [sse, setSse] = useState(false);

  // Clears every trace of the session in this tab. Used by an explicit log out and by any 401.
  const signOutLocally = useCallback(() => {
    try { localStorage.removeItem("token"); } catch { /* already gone */ }
    setAuthState("out");
    setAccount(null);
    setItems([]);
    setChanges([]);
    setCohorts([]);
    setLoaded(false);
    setExplainSymbol(null);
    setDemoOpen(false);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const st = await api.getState(); // single round trip: market + watchlist + changes
      setItems(st.items);
      setChanges(st.changes);
      setCohorts(st.cohorts || []);
      setMarket(st.market);
      setError(null);
    } catch (err) {
      if (err.status === 401) signOutLocally();
      else setError(err.message || "Couldn't refresh your watchlist");
    } finally {
      setLoaded(true);
    }
  }, [signOutLocally]);

  // A remembered token is only a claim until the server agrees. Validate once on load.
  useEffect(() => {
    if (authState !== "checking") return;
    let alive = true;
    api
      .me()
      .then((me) => { if (alive) { setAccount(me); setAuthState("in"); } })
      .catch(() => { if (alive) signOutLocally(); });
    return () => { alive = false; };
  }, [authState, signOutLocally]);

  useEffect(() => {
    if (authState !== "in") return;
    refresh();
    const id = setInterval(refresh, sse ? POLL_MS_WITH_SSE : POLL_MS);
    return () => clearInterval(id);
  }, [authState, refresh, sse]);

  // SSE: no user data on the stream, so no token in the URL; each tick just triggers an authed refetch.
  useEffect(() => {
    if (authState !== "in" || typeof EventSource === "undefined") return;
    let es;
    try {
      es = new EventSource(`${API_BASE}/events`);
    } catch {
      return;
    }
    es.onopen = () => setSse(true);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (ev.type === "quotes_updated") refresh();
      } catch { /* ignore malformed */ }
    };
    es.onerror = () => setSse(false); // browser auto-reconnects; polling covers the gap
    return () => { es.close(); setSse(false); };
  }, [authState, refresh]);

  // Any action that fails (offline, server restart, unknown symbol) surfaces as a toast instead of a
  // dead click. Returns whether it worked, so callers can keep the user's input on failure.
  const guard = (fn) => async (...args) => {
    try {
      await fn(...args);
      await refresh();
      return true;
    } catch (err) {
      if (err.status === 401) signOutLocally();
      else setError(err.message || "Something went wrong");
      return false;
    }
  };

  const handleAdd = guard((symbol) => api.addSymbol(symbol));
  const handleSeen = guard((symbol) => api.markSeen(symbol));
  const handleRemove = guard((symbol) => api.removeSymbol(symbol));
  const handleMarkAll = guard(() => api.markAllSeen());
  const handleSetQuantity = guard((symbol, quantity) => api.setQuantity(symbol, quantity));
  const handleSnooze = guard((symbol, minutes) => api.snooze(symbol, minutes));
  // Demo: re-create "you last looked 15 minutes ago" from the simulator's deterministic history.
  const handleRewind = guard(() => api.rewind(15));

  async function handleLogout() {
    await api.logout();
    signOutLocally();
  }

  // Prefer the digest row (it carries headline + peer z); fall back to the watchlist row.
  const explainRow =
    (explainSymbol && changes.find((c) => c.symbol === explainSymbol)) ||
    (explainSymbol &&
      items.find((i) => i.symbol === explainSymbol && i.signal && i.change_since_seen && i.last_seen)) ||
    null;

  // "Mark all seen" empties `changes` while the panel is open — close it rather than strand a symbol.
  useEffect(() => {
    if (explainSymbol && !explainRow) setExplainSymbol(null);
  }, [explainSymbol, explainRow]);

  // Stable identities: the drawer's open/close effect keys on these, and a new closure every 5s poll
  // would re-run it and yank focus out from under a keyboard user.
  const closeExplain = useCallback(() => setExplainSymbol(null), []);
  const closeDemo = useCallback(() => setDemoOpen(false), []);
  const closeLegend = useCallback(() => setLegendOpen(false), []);

  if (authState === "checking") {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <span
          role="status"
          aria-label="Loading"
          className="w-6 h-6 rounded-full border-2 border-line border-t-brand animate-spin"
        />
      </div>
    );
  }

  if (authState === "out") {
    return <AuthScreen onAuthed={(a) => { setAccount(a); setAuthState("in"); }} />;
  }

  // Simulated data gates the demo tools: never offer to inject garbage into a live feed.
  const simulated = items.some((i) => i.provenance?.is_simulated);
  // One source for the whole page -> the summary strip names it once. Mixed (the composite provider
  // serving some symbols live and some from the fallback) -> every price carries its own source chip.
  const mixedSources = new Set(items.map((i) => !!i.provenance?.is_simulated)).size > 1;
  const loading = !loaded;

  return (
    <div className="min-h-screen">
      <TopBar
        tab={tab}
        onTab={setTab}
        market={market}
        account={account}
        theme={theme}
        onToggleTheme={toggleTheme}
        live={sse}
        showDemo={simulated && items.length > 0}
        onDemo={() => setDemoOpen(true)}
        onLegend={() => setLegendOpen(true)}
        onLogout={handleLogout}
      />

      <main className="mx-auto max-w-[1400px] px-4 lg:px-6 py-4 lg:py-6">
        {tab === "watchlist" ? (
          <>
            <SummaryStrip items={items} changes={changes} loading={loading} />
            <div className="grid gap-6 lg:grid-cols-12 items-start">
              <div className="lg:col-span-7 xl:col-span-8 min-w-0">
                <DigestFeed
                  changes={changes}
                  cohorts={cohorts}
                  mixedSources={mixedSources}
                  loading={loading}
                  onMarkAllSeen={handleMarkAll}
                  onSnooze={handleSnooze}
                  onExplain={setExplainSymbol}
                />
              </div>
              <div className="lg:col-span-5 xl:col-span-4 min-w-0 lg:sticky lg:top-[4.5rem]">
                <WatchlistPanel
                  items={items}
                  digestSymbols={changes.map((c) => c.symbol)}
                  mixedSources={mixedSources}
                  loading={loading}
                  onAdd={handleAdd}
                  onSeen={handleSeen}
                  onRemove={handleRemove}
                  onSetQuantity={handleSetQuantity}
                  onExplain={setExplainSymbol}
                />
              </div>
            </div>
          </>
        ) : (
          <HowItWorks cohorts={cohorts} items={items} market={market} />
        )}
      </main>

      <Drawer
        open={!!explainRow}
        onClose={closeExplain}
        title={explainRow ? displaySymbol(explainRow.symbol) : ""}
        subtitle={explainRow?.headline || "Why this showed up"}
      >
        {explainRow && <ExplainPanel row={explainRow} />}
      </Drawer>

      {simulated && (
        <DemoDrawer
          open={demoOpen}
          onClose={closeDemo}
          symbols={items.map((i) => i.symbol)}
          onRewind={handleRewind}
          onDone={refresh}
        />
      )}

      <Legend open={legendOpen} onClose={closeLegend} />

      <Toast message={error} onDismiss={() => setError(null)} />
    </div>
  );
}
