import { useState } from "react";
import { api } from "../api.js";

export default function Login({ onLoggedIn }) {
  const [username, setUsername] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const data = await api.login(username.trim(), pin.trim());
      onLoggedIn(data.username);
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-white rounded-2xl shadow-sm border border-slate-100 p-6">
        <div className="flex items-center gap-2 mb-1">
          <span className="w-2.5 h-2.5 rounded-full bg-brand" />
          <h1 className="text-xl font-semibold">Signal</h1>
        </div>
        <p className="text-sm text-slate-500 mb-6">Your watchlist, but it tells you what actually changed.</p>

        <label className="block text-sm font-medium text-slate-700 mb-1">Username</label>
        <input
          className="w-full mb-4 px-3 py-2.5 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-brand/40"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoCapitalize="none"
          placeholder="e.g. asha"
        />

        <label className="block text-sm font-medium text-slate-700 mb-1">PIN</label>
        <input
          className="w-full mb-2 px-3 py-2.5 rounded-lg border border-slate-200 focus:outline-none focus:ring-2 focus:ring-brand/40"
          value={pin}
          onChange={(e) => setPin(e.target.value)}
          type="password"
          inputMode="numeric"
          placeholder="choose / enter your PIN"
        />
        <p className="text-xs text-slate-400 mb-5">
          First time? This creates your account. It's how your watchlist follows you across devices.
        </p>

        {error && <p className="text-sm text-down mb-3">{error}</p>}

        <button
          disabled={busy}
          className="w-full py-2.5 rounded-lg bg-brand text-white font-medium hover:bg-brand-dark disabled:opacity-60 transition"
        >
          {busy ? "…" : "Continue"}
        </button>
      </form>
    </div>
  );
}
