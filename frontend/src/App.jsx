import { useCallback, useEffect, useState } from "react";
import { api, getToken } from "./api.js";
import Login from "./components/Login.jsx";
import Digest from "./components/Digest.jsx";
import Watchlist from "./components/Watchlist.jsx";
import ModelPanel from "./components/ModelPanel.jsx";
import { MarketPill } from "./components/Badges.jsx";

const POLL_MS = 5000; // M1: client polling. SSE replaces this in M6 behind the same refresh seam.

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
        api.logout();
        setAuthed(false);
      } else {
        setError(err.message || "Failed to load");
      }
    }
  }, []);

  useEffect(() => {
    if (!authed) return;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
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
  function handleLogout() {
    api.logout();
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
        <Digest changes={changes} onMarkAllSeen={handleMarkAll} />
        <Watchlist items={items} onAdd={handleAdd} onSeen={handleSeen} onRemove={handleRemove} />
        {simulated && items.length > 0 && (
          <button
            onClick={handleRewind}
            className="mt-4 w-full text-xs text-slate-500 hover:text-slate-800 py-2"
            title="Simulated data: re-create your snapshots as of 15 minutes ago to see 'While you were away' on demand"
          >
            ⟲ Demo: pretend I last looked 15 minutes ago
          </button>
        )}
        <ModelPanel />
      </main>
    </div>
  );
}
