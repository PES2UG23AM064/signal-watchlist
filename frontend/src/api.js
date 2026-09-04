// Thin API client. Base URL comes from VITE_API_URL; the bearer token lives in localStorage so a
// returning user stays signed in on this device.

const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";
export const API_BASE = BASE;

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
    /* storage blocked — the app still works for this session */
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
  async register(email, password, displayName) {
    const body = { email, password };
    if (displayName) body.display_name = displayName;
    const data = await req("/auth/register", { method: "POST", body, auth: false });
    setToken(data.token);
    return data;
  },
  async login(email, password) {
    const data = await req("/auth/login", { method: "POST", body: { email, password }, auth: false });
    setToken(data.token);
    return data;
  },
  me: () => req("/auth/me"),
  async logout() {
    // Server-side first (rotates the token so it's dead everywhere), then forget it locally.
    try {
      await req("/auth/logout", { method: "POST" });
    } catch {
      /* already invalid/expired — local clear is still correct */
    }
    setToken(null);
  },
  getState: () => req("/state"), // market + watchlist + ranked changes in one round trip
  getModel: () => req("/model", { auth: false }), // backtest receipts
  getStatus: () => req("/status", { auth: false }), // provider route, poller lag, freshness
  rewind: (minutes = 15) => req(`/dev/rewind?minutes=${minutes}`, { method: "POST" }), // demo only
  getMarket: () => req("/market", { auth: false }),
  getWatchlist: () => req("/watchlist"),
  addSymbol: (symbol) => req("/watchlist", { method: "POST", body: { symbol } }),
  removeSymbol: (symbol) => req(`/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" }),
  markSeen: (symbol) => req(`/watchlist/${encodeURIComponent(symbol)}/seen`, { method: "POST" }),
  snooze: (symbol, minutes = 60) =>
    req(`/watchlist/${encodeURIComponent(symbol)}/snooze?minutes=${minutes}`, { method: "POST" }),
  setQuantity: (symbol, quantity) =>
    req(`/watchlist/${encodeURIComponent(symbol)}/quantity`, { method: "PATCH", body: { quantity } }),
  inject: (symbol, kind) => req("/dev/inject", { method: "POST", body: { symbol, kind } }), // demo only
  markAllSeen: () => req("/watchlist/seen-all", { method: "POST" }),
};
