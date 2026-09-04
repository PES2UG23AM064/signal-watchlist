import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { applyTheme, initialTheme } from "./theme.js";
import "./index.css";

// Before the first paint, so the page never flashes the wrong colour.
applyTheme(initialTheme());

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
