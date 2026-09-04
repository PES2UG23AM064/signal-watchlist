import { useCallback, useEffect, useState } from "react";

// Dark is the default; an explicit choice is remembered per device. No prefers-color-scheme fallback
// for the initial value: most desktops report "light" and the product is designed dark-first.
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

// Also called before React mounts (main.jsx) so the first paint never flashes white.
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
