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

// SSE pushes a "quotes updated" tick after every poll cycle and we refetch /state on it. Polling
// stays as the fallback, slower once SSE is connected.
const POLL_MS = 5000;
const POLL_MS_WITH_SSE = 30000;

export default function App() {
  // "checking" until /auth/me confirms a remembered token — the dashboard never renders on an unverified one.
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
  // The drawer holds a symbol, not a row object: the row is re-derived from live data each render, so an
  // open panel keeps updating and can't show a row that's gone.
  const [explainSymbol, setExplainSymbol] = useState(null);
  const [demoOpen, setDemoOpen] = useState(false);
  const [legendOpen, setLegendOpen] = useState(false);
  const [sse, setSse] = useState(false);

  // Used by an explicit log out and by any 401.
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
      const st = await api.getState();
      setItems(st.items);
      setChanges(st.changes);
      setCohorts(st.cohorts || []);
      setMarket(st.market);
      // A good refresh clears only a refresh failure; an action's error must survive the next background
      // tick or the user never gets to read it.
      setError((e) => (e && e.source === "refresh" ? null : e));
    } catch (err) {
      if (err.status === 401) signOutLocally();
      else setError({ message: err.message || "Couldn't refresh your watchlist", source: "refresh" });
    } finally {
      setLoaded(true);
    }
  }, [signOutLocally]);

  // A remembered token is only a claim until the server agrees.
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

  // No user data on the stream, so no token in the URL; each tick just triggers an authed refetch.
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

  // Failed actions surface as a toast. Returns whether it worked so callers can keep the user's input on failure.
  const guard = (fn) => async (...args) => {
    try {
      await fn(...args);
      await refresh();
      return true;
    } catch (err) {
      if (err.status === 401) signOutLocally();
      else setError({ message: err.message || "Something went wrong", source: "action" });
      return false;
    }
  };

  const handleAdd = guard((symbol) => api.addSymbol(symbol));
  const handleSeen = guard((symbol) => api.markSeen(symbol));
  const handleRemove = guard((symbol) => api.removeSymbol(symbol));
  const handleMarkAll = guard(() => api.markAllSeen());
  const handleSetQuantity = guard((symbol, quantity) => api.setQuantity(symbol, quantity));
  const handleSnooze = guard((symbol, minutes) => api.snooze(symbol, minutes));
  // Demo: re-creates "you last looked 15 minutes ago" from the simulator's history.
  const handleRewind = guard(() => api.rewind(15));

  async function handleLogout() {
    await api.logout();
    signOutLocally();
  }

  // Prefer the digest row (it carries the headline and peer z); fall back to the watchlist row.
  const explainRow =
    (explainSymbol && changes.find((c) => c.symbol === explainSymbol)) ||
    (explainSymbol &&
      items.find((i) => i.symbol === explainSymbol && i.signal && i.change_since_seen && i.last_seen)) ||
    null;

  // "Mark all seen" empties `changes` while the panel is open — close it rather than strand a symbol.
  useEffect(() => {
    if (explainSymbol && !explainRow) setExplainSymbol(null);
  }, [explainSymbol, explainRow]);

  // Stable identities: the drawer's effect keys on these, and a new closure every poll would re-run it
  // and yank focus from a keyboard user.
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

  // Demo tools are only offered on simulated data, never against a live feed.
  const simulated = items.some((i) => i.provenance?.is_simulated);
  // One source for the page: the summary strip names it once. Mixed: every price carries its own source chip.
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

      <Toast message={error?.message} onDismiss={() => setError(null)} />
    </div>
  );
}
