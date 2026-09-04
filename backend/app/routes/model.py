"""Serves the committed backtest report: static JSON produced offline by ml/train_scorer.py on real candles.

Its conclusion (no predictive label clears the bar) is why no learned model runs in this product.
"""
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
    """Backtest report plus cohort validation. No model is shipped: see report.conclusion."""
    report = _read("backtest_report.json")
    report["shipped_model"] = None
    try:
        report["cohorts"] = _read("cohort_report.json")
    except HTTPException:
        report["cohorts"] = None
    return report
