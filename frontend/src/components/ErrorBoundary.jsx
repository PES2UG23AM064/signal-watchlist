import { Component } from "react";

// A render error anywhere below this point would otherwise leave a blank white page — the worst
// possible failure for a product whose whole claim is "we tell you the truth about your data".
// Instead: say what happened, keep the app recoverable, and never pretend the numbers are fine.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // In production this is where a real deployment would ship to Sentry/Datadog. Console keeps the
    // stack reachable in a demo without adding a dependency.
    console.error("Signal crashed while rendering:", error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="min-h-screen flex items-center justify-center px-4">
        <div className="card max-w-md w-full p-6 text-center">
          <div className="w-11 h-11 rounded-full bg-down/10 text-down mx-auto flex items-center justify-center text-xl">
            !
          </div>
          <h1 className="font-semibold text-ink mt-3">Something broke on this screen</h1>
          <p className="text-sm text-ink-3 mt-1.5 leading-relaxed">
            Your watchlist and your baselines are safe — they live on the server, not in this page.
            Reloading will pick up where you left off.
          </p>
          <button onClick={() => window.location.reload()} className="btn btn-primary btn-md w-full mt-5">
            Reload
          </button>
          <p className="text-2xs text-ink-4 mt-3 font-mono break-words">
            {String(this.state.error?.message || this.state.error)}
          </p>
        </div>
      </div>
    );
  }
}
