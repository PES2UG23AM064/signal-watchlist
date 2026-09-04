import { useCallback, useEffect, useState } from "react";

// Dark is the product default. A viewer who explicitly picks light gets light, remembered on this
// device. We deliberately do NOT fall back to prefers-color-scheme for the initial value: most
// desktops report "light", which would mean the product almost never opened in the theme it was
// designed for. The OS preference is honoured the moment the user expresses one via the toggle.
const KEY = "theme";

export function initialTheme() {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* storage blocked — fall through to the default */
  }
  return "dark";
}

// Applied before React mounts (see main.jsx) so the first paint is already the right colour and the
// page never flashes white.
export function applyTheme(theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function useTheme() {
  const [theme, setTheme] = useState(initialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch { /* ignore */ }
      return next;
    });
  }, []);

  return [theme, toggle];
}
