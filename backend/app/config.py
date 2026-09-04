"""Application settings, loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    cors_origins: str = "http://localhost:5173"
    # Also allow this project's own Render deployments without hardcoding the exact URL (the frontend
    # and API get separate onrender.com subdomains), and any local dev origin — `127.0.0.1:5173` must
    # work exactly like `localhost:5173`, or a browser that picked one reads "Failed to fetch".
    cors_origin_regex: str = r"^(https://.*\.onrender\.com|http://(localhost|127\.0\.0\.1)(:\d+)?)$"
    market_provider: str = "replay"
    replay_seed: int = 42

    # Optional SECOND real feed for cross-source reconciliation (Twelve Data; free key). Its quotes are a
    # cross-check only — never served. Divergence beyond the threshold within the window => "disputed".
    twelvedata_api_key: str | None = None
    dispute_threshold_pct: float = 2.0
    dispute_window_seconds: int = 120

    # Dev/demo endpoints (e.g. POST /dev/rewind). Auth-scoped and Replay-only; on for the hackathon demo.
    enable_dev_endpoints: bool = True

    # Poller
    poll_interval_seconds: float = 5.0
    prune_interval_seconds: float = 120.0
    quote_retention_hours: int = 24
    run_poller: bool = True  # in-process poller in the API lifespan (single-container deploy)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
