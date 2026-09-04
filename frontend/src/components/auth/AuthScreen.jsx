import { useEffect, useRef, useState } from "react";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import { api } from "../../api.js";

// Sign in / create account. Server-side validation is surfaced inline on the field it belongs to.

const MIN_PASSWORD = 8;

function strengthOf(pw) {
  if (pw.length < MIN_PASSWORD) return { pct: Math.min(90, (pw.length / MIN_PASSWORD) * 60), label: `${MIN_PASSWORD - pw.length} more character${MIN_PASSWORD - pw.length === 1 ? "" : "s"}`, tone: "bg-down" };
  let score = 1;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) score++;
  if (/\d/.test(pw)) score++;
  if (/[^\w\s]/.test(pw) || pw.length >= 14) score++;
  const map = {
    1: { pct: 45, label: "Long enough", tone: "bg-delayed" },
    2: { pct: 70, label: "Good", tone: "bg-delayed" },
    3: { pct: 88, label: "Strong", tone: "bg-up" },
    4: { pct: 100, label: "Very strong", tone: "bg-up" },
  };
  return map[score];
}

// The server owns validation. We only decide which field a message belongs under.
function fieldFor(detail, status) {
  const d = String(detail || "").toLowerCase();
  if (status === 409) return "email";
  // 401 and 429 are deliberately vague about which credential is wrong, so they can't point at a field.
  if (status === 401 || status === 429) return "form";
  if (d.includes("email")) return "email";
  if (d.includes("password")) return "password";
  if (d.includes("name")) return "name";
  return "form";
}

export default function AuthScreen({ onAuthed }) {
  const [mode, setMode] = useState("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [err, setErr] = useState(null); // { field, message }
  const [busy, setBusy] = useState(false);
  const emailRef = useRef(null);

  const creating = mode === "create";
  const strength = creating && password ? strengthOf(password) : null;

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  function switchMode(next) {
    setMode(next);
    setErr(null);
  }

  async function submit(e) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const data = creating
        ? await api.register(email.trim(), password, name.trim() || undefined)
        : await api.login(email.trim(), password);
      onAuthed(data);
    } catch (ex) {
      const message =
        ex.status === 409
          ? "An account with this email already exists — sign in instead."
          : ex.message || "Something went wrong. Try again.";
      setErr({ field: fieldFor(ex.message, ex.status), message });
      if (ex.status === 409) setMode("signin");
    } finally {
      setBusy(false);
    }
  }

  const errFor = (field) => (err?.field === field ? err.message : null);
  const canSubmit = email.trim() && password && (!creating || password.length >= MIN_PASSWORD);

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[1.1fr_1fr]">
      <aside className="hidden lg:flex flex-col justify-center px-14 xl:px-24 bg-surface2 border-r border-line">
        <div className="flex items-center gap-2.5">
          <span className="w-3 h-3 rounded-full bg-brand" />
          <span className="text-lg font-semibold tracking-tight">Signal</span>
        </div>
        <h1 className="text-4xl font-semibold tracking-tight mt-8 leading-[1.15] max-w-lg text-ink">
          Your watchlist, but it tells you what actually changed.
        </h1>
        <p className="text-base text-ink-3 mt-5 max-w-md leading-relaxed">
          Open it after a few hours away and see the few things worth your attention — ranked, explained,
          and honest about how fresh the numbers are.
        </p>
      </aside>

      <div className="min-h-screen lg:min-h-0 flex items-center justify-center px-4 py-10">
        <form onSubmit={submit} noValidate className="w-full max-w-sm">
          <div className="flex items-center gap-2 mb-6 lg:hidden">
            <span className="w-2.5 h-2.5 rounded-full bg-brand" />
            <span className="font-semibold tracking-tight">Signal</span>
          </div>

          <h2 className="text-xl font-semibold tracking-tight text-ink">
            {creating ? "Create your account" : "Welcome back"}
          </h2>
          <p className="text-sm text-ink-3 mt-1 mb-5">
            {creating ? "It takes a moment, and your watchlist follows you everywhere." : "Sign in to pick up where you left off."}
          </p>

          <div role="tablist" aria-label="Account" className="flex gap-0.5 p-0.5 rounded-lg bg-surface2 mb-5">
            {[["signin", "Sign in"], ["create", "Create account"]].map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={mode === id}
                onClick={() => switchMode(id)}
                className={`grow text-xs font-medium px-3 py-2 rounded-[6px] transition-colors duration-150 ${
                  mode === id ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <label htmlFor="email" className="block text-sm font-medium text-ink-2 mb-1.5">Email</label>
          <input
            ref={emailRef}
            id="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck="false"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            aria-invalid={!!errFor("email")}
            className={`field ${errFor("email") ? "field-error" : ""}`}
          />
          {errFor("email") && <p className="text-xs text-down mt-1.5">{errFor("email")}</p>}

          {creating && (
            <>
              <label htmlFor="name" className="block text-sm font-medium text-ink-2 mb-1.5 mt-4">
                Name <span className="text-ink-4 font-normal">optional</span>
              </label>
              <input
                id="name"
                type="text"
                autoComplete="name"
                maxLength={60}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="What should we call you?"
                aria-invalid={!!errFor("name")}
                className={`field ${errFor("name") ? "field-error" : ""}`}
              />
              {errFor("name") && <p className="text-xs text-down mt-1.5">{errFor("name")}</p>}
            </>
          )}

          <label htmlFor="password" className="block text-sm font-medium text-ink-2 mb-1.5 mt-4">
            Password
          </label>
          <div className="relative">
            <input
              id="password"
              type={showPw ? "text" : "password"}
              autoComplete={creating ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={creating ? `At least ${MIN_PASSWORD} characters` : "Your password"}
              aria-invalid={!!errFor("password")}
              className={`field pr-11 ${errFor("password") ? "field-error" : ""}`}
            />
            <button
              type="button"
              onClick={() => setShowPw(!showPw)}
              aria-label={showPw ? "Hide password" : "Show password"}
              className="absolute right-1 top-1/2 -translate-y-1/2 btn btn-ghost p-2"
            >
              {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
          {errFor("password") && <p className="text-xs text-down mt-1.5">{errFor("password")}</p>}

          {strength && (
            <div className="mt-2.5 flex items-center gap-2.5">
              <div className="h-1 grow rounded-full bg-surface2 overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-200 ${strength.tone}`}
                  style={{ width: `${strength.pct}%` }}
                />
              </div>
              <span className="text-2xs text-ink-3 w-28 shrink-0">{strength.label}</span>
            </div>
          )}

          {errFor("form") && (
            <p role="alert" className="text-sm text-down bg-down/10 rounded-lg px-3 py-2 mt-4">
              {errFor("form")}
            </p>
          )}

          <button disabled={busy || !canSubmit} className="btn btn-primary btn-md w-full mt-6">
            {busy && <Loader2 size={16} className="animate-spin" />}
            {busy ? (creating ? "Creating…" : "Signing in…") : creating ? "Create account" : "Sign in"}
          </button>

          <p className="text-xs text-ink-4 mt-4 text-center">
            {creating ? "Already have an account? " : "New here? "}
            <button
              type="button"
              onClick={() => switchMode(creating ? "signin" : "create")}
              className="text-brand-dark dark:text-brand font-medium hover:underline"
            >
              {creating ? "Sign in" : "Create one"}
            </button>
          </p>
        </form>
      </div>
    </div>
  );
}
