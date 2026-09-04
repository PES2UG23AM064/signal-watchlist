import { useCallback, useEffect, useState } from "react";
import { api, getToken, API_BASE } from "./api.js";
import Login from "./components/Login.jsx";
import Digest from "./components/Digest.jsx";
import Watchlist from "./components/Watchlist.jsx";
import ModelPanel from "./components/ModelPanel.jsx";
import FaultPanel from "./components/FaultPanel.jsx";
import StatusPanel from "./components/StatusPanel.jsx";
import { MarketPill } from "./components/Badges.jsx";

// Transport: Server-Sent Events push a "quotes updated" tick after every poll cycle, and we refetch
// /state on each tick. Polling stays as the FALLBACK (slower once SSE is connected) — same refresh seam.
const POLL_MS = 5000;
const POLL_MS_WITH_SSE = 30000;

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [username, setUsername] = useState("");
  const [items, setItems] = useState([]);
  const [changes, setChanges] = useState([]);
  const [market, setMarket] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const st = await api.getState(); // single round trip: market + watchlist + changes
      setItems(st.items);
      setChanges(st.changes);
      setMarket(st.market);
      setError(null);
    } catch (err) {
      if (err.status === 401) {
        // token unknown or expired: forget it locally (no server call — it's already invalid)
        try { localStorage.removeItem("token"); } catch { /* ignore */ }
        setAuthed(false);
      } else {
        setError(err.message || "Failed to load");
      }
    }
  }, []);

  const [sse, setSse] = useState(false);

  useEffect(() => {
    if (!authed) return;
    refresh();
    const id = setInterval(refresh, sse ? POLL_MS_WITH_SSE : POLL_MS);
    return () => clearInterval(id);
  }, [authed, refresh, sse]);

  // SSE: no user data on the stream, so no token in the URL; each tick just triggers an authed refetch.
  useEffect(() => {
    if (!authed || typeof EventSource === "undefined") return;
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
  }, [authed, refresh]);

  async function handleAdd(symbol) {
    await api.addSymbol(symbol);
    await refresh();
  }
  async function handleSeen(symbol) {
    await api.markSeen(symbol);
    await refresh();
  }
  async function handleRemove(symbol) {
    await api.removeSymbol(symbol);
    await refresh();
  }
  async function handleMarkAll() {
    await api.markAllSeen();
    await refresh();
  }
  async function handleSetQuantity(symbol, quantity) {
    await api.setQuantity(symbol, quantity);
    await refresh();
  }
  async function handleSnooze(symbol, minutes) {
    await api.snooze(symbol, minutes);
    await refresh();
  }
  // Demo: re-create "you last looked 15 minutes ago" from the simulator's deterministic history.
  async function handleRewind() {
    try {
      await api.rewind(15);
      await refresh();
    } catch (err) {
      setError(err.message || "Rewind failed");
    }
  }
  const simulated = items.some((i) => i.provenance?.is_simulated);
  async function handleLogout() {
    await api.logout();
    setAuthed(false);
    setItems([]);
    setChanges([]);
  }

  if (!authed) {
    return (
      <Login
        onLoggedIn={(u) => {
          setUsername(u);
          setAuthed(true);
        }}
      />
    );
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 bg-slate-50/80 backdrop-blur border-b border-slate-100 z-10">
        <div className="max-w-md mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-brand" />
            <span className="font-semibold">Signal</span>
          </div>
          <div className="flex items-center gap-3">
            <MarketPill market={market} />
            <button onClick={handleLogout} className="text-xs text-slate-400 hover:text-slate-700">
              {username ? `${username} · ` : ""}Log out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-md mx-auto px-4 py-5">
        {error && (
          <div className="mb-4 text-sm text-down bg-down/5 border border-down/20 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
        <Digest changes={changes} onMarkAllSeen={handleMarkAll} onSnooze={handleSnooze} />
        <Watchlist items={items} onAdd={handleAdd} onSeen={handleSeen} onRemove={handleRemove} onSetQuantity={handleSetQuantity} />
        {simulated && items.length > 0 && (
          <>
            <button
              onClick={handleRewind}
              className="mt-4 w-full text-xs text-slate-500 hover:text-slate-800 py-2"
              title="Simulated data: re-create your snapshots as of 15 minutes ago to see 'While you were away' on demand"
            >
              ⟲ Demo: pretend I last looked 15 minutes ago
            </button>
            <FaultPanel symbols={items.map((i) => i.symbol)} onDone={refresh} />
          </>
        )}
        <ModelPanel />
        <StatusPanel />
      </main>
    </div>
  );
}
