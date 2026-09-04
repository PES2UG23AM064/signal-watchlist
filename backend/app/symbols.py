"""Symbol normalization so 'reliance', 'RELIANCE', 'reliance.ns' all resolve to one canonical key."""
from __future__ import annotations

import re

# NSE tickers: letters, digits, '&' (M&M), '-' (BAJAJ-AUTO); an optional exchange suffix; '^' for an index.
_VALID = re.compile(r"^\^?[A-Z0-9&\-]{1,20}(\.[A-Z]{1,4})?$")


class InvalidSymbol(ValueError):
    """Not something that could be a ticker (empty, spaces, punctuation...). Mapped to HTTP 400."""


def normalize(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not s:
        raise InvalidSymbol("enter a symbol, e.g. RELIANCE")
    if not _VALID.match(s):
        raise InvalidSymbol(f"'{raw.strip()}' doesn't look like a ticker — try something like RELIANCE or TCS")
    if s.startswith("^"):  # index, e.g. ^NSEI
        return s
    if "." not in s:  # default to NSE for the Groww/Indian-market demo
        s += ".NS"
    return s
