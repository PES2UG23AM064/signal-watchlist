"""Symbol normalization so 'reliance', 'RELIANCE', 'reliance.ns' all resolve to one canonical key."""
from __future__ import annotations


def normalize(raw: str) -> str:
    s = raw.strip().upper()
    if not s:
        raise ValueError("empty symbol")
    if s.startswith("^"):  # index, e.g. ^NSEI
        return s
    if "." not in s:  # default to NSE for the Groww/Indian-market demo
        s += ".NS"
    return s
