import { useCallback, useEffect, useState } from "react";
import { api, getToken } from "./api.js";
import Login from "./components/Login.jsx";
import Digest from "./components/Digest.jsx";
import Watchlist from "./components/Watchlist.jsx";

const POLL_MS = 5000; // M1: client polling. SSE replaces this in M6 behind the same refresh seam.

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [username, setUsername] = useState("");
  const [items, setItems] = useState([]);
  const [changes, setChanges] = useState([]);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [wl, ch] = await Promise.all([api.getWatchlist(), api.getChanges()]);
      setItems(wl);
      setChanges(ch);
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
          <button onClick={handleLogout} className="text-xs text-slate-400 hover:text-slate-700">
            {username ? `${username} · ` : ""}Log out
          </button>
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
      </main>
    </div>
  );
}
