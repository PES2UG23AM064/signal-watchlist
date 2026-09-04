// Thin API client. Base URL comes from VITE_API_URL (set per-environment); token is the bearer
// session token persisted in localStorage so a returning user stays logged in on this device.

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export function getToken() {
  try {
    return localStorage.getItem("token");
  } catch {
    return null;
  }
}

function setToken(t) {
  try {
    if (t) localStorage.setItem("token", t);
    else localStorage.removeItem("token");
  } catch {
    /* private mode / storage blocked — app still works for this session */
  }
}

async function req(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const t = getToken();
    if (t) headers["Authorization"] = `Bearer ${t}`;
  }
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* non-JSON error */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  async login(username, pin) {
    const data = await req("/auth/login", { method: "POST", body: { username, pin }, auth: false });
    setToken(data.token);
    return data;
  },
  logout() {
    setToken(null);
  },
  getWatchlist: () => req("/watchlist"),
  addSymbol: (symbol) => req("/watchlist", { method: "POST", body: { symbol } }),
  removeSymbol: (symbol) => req(`/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" }),
  markSeen: (symbol) => req(`/watchlist/${encodeURIComponent(symbol)}/seen`, { method: "POST" }),
  markAllSeen: () => req("/watchlist/seen-all", { method: "POST" }),
  getChanges: () => req("/changes"),
};
