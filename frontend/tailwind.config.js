/** @type {import('tailwindcss').Config} */

// Every colour in the app resolves to a CSS variable defined in index.css, so light and dark are the
// same components with a different token set — never duplicated `dark:` classes on every element.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // Surfaces and text
        app: token("bg"),
        surface: token("surface"),
        surface2: token("surface-2"),
        line: token("border"),
        "line-strong": token("border-strong"),
        ink: token("text"),
        "ink-2": token("text-2"),
        "ink-3": token("text-3"),
        "ink-4": token("text-4"),

        // Brand + direction. `up` is the brand green; `fresh` is deliberately cyan so "this price is
        // live" can never be misread as "this stock is up".
        brand: { DEFAULT: token("brand"), dark: token("brand-dark") },
        up: token("up"),
        down: token("down"),

        // Data-state semantics
        fresh: token("fresh"),
        delayed: token("delayed"),
        stale: token("stale"),
        sim: token("sim"),
        cohort: token("cohort"),
        alone: token("alone"),
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }], // 11px — badges, meta
      },
      borderRadius: {
        card: "0.75rem",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        lift: "var(--shadow-lift)",
        drawer: "var(--shadow-drawer)",
      },
      transitionDuration: {
        DEFAULT: "180ms",
      },
      keyframes: {
        "slide-up": { from: { transform: "translateY(100%)" }, to: { transform: "translateY(0)" } },
        "slide-left": { from: { transform: "translateX(100%)" }, to: { transform: "translateX(0)" } },
        "fade-in": { from: { opacity: 0 }, to: { opacity: 1 } },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        spin: { to: { transform: "rotate(360deg)" } },
      },
      animation: {
        "slide-up": "slide-up 200ms cubic-bezier(0.32, 0.72, 0, 1)",
        "slide-left": "slide-left 200ms cubic-bezier(0.32, 0.72, 0, 1)",
        "fade-in": "fade-in 150ms ease-out",
        spin: "spin 700ms linear infinite",
      },
    },
  },
  plugins: [],
};
