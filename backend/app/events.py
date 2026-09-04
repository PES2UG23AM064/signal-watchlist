"""In-process event bus — the fan-out seam.

Single process by design: the poller publishes "quotes updated" after each cycle and every open SSE
connection (see routes/events.py) receives it. This is deliberately the simplest thing that works at one
instance. The multi-instance version swaps this module for Postgres LISTEN/NOTIFY (session-mode
connection required on Supabase) — same publish/subscribe surface, different transport.

Events carry NO user data (just "something changed" + counts), so the stream needs no authentication
and no token ever appears in a URL; clients refetch the authenticated /state on each tick.
"""
from __future__ import annotations

import asyncio
from typing import Any

_subscribers: set[asyncio.Queue] = set()
QUEUE_SIZE = 100


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: dict[str, Any]) -> int:
    """Non-blocking fan-out. A slow consumer's full queue drops the tick (it will catch up on the next
    one) rather than stalling the poller."""
    delivered = 0
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
            delivered += 1
        except asyncio.QueueFull:
            pass
    return delivered


def subscriber_count() -> int:
    return len(_subscribers)
