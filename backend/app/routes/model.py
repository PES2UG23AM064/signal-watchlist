"""Serves the committed backtest report — the receipts behind 'meaningful'.

Static JSON produced offline by ml/train_scorer.py on REAL NSE candles (never the replay simulator).
It reports all three pre-registered labels, including the two that showed no edge."""
from __future__ import annotations

import json
import pathlib

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["model"])

_MODEL_DIR = pathlib.Path(__file__).resolve().parent.parent / "model"


def _read(name: str) -> dict:
    try:
        return json.loads((_MODEL_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, f"{name} not available")


@router.get("/model")
async def model_report() -> dict:
    """Backtest report + the shipped model's coefficients, for the 'receipts' panel."""
    report = _read("backtest_report.json")
    try:
        shipped = _read("scoring_model.json")
        report["shipped_model"] = {k: shipped[k] for k in ("version", "label", "features", "coef", "intercept")}
    except HTTPException:
        report["shipped_model"] = None
    return report
