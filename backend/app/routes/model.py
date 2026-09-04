"""Serves the committed backtest report — the receipts behind 'meaningful'.

Static JSON produced offline by ml/train_scorer.py on REAL NSE candles (never the replay simulator).
It reports all three pre-registered predictive labels with block-bootstrap confidence intervals — and the
conclusion that none clears the bar to ship, which is WHY no learned model runs in this product."""
from __future__ import annotations

import json
import pathlib

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["model"])

_MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "model"


def _read(name: str) -> dict:
    try:
        return json.loads((_MODEL_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(404, f"{name} not available") from e


@router.get("/model")
async def model_report() -> dict:
    """Backtest report (+ cohort validation), for the 'receipts' panel. No model is shipped: see report.conclusion."""
    report = _read("backtest_report.json")
    report["shipped_model"] = None
    try:
        report["cohorts"] = _read("cohort_report.json")  # co-movement cohorts: structure, stability, alert replay
    except HTTPException:
        report["cohorts"] = None
    return report
