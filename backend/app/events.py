"""In-process event bus: the poller publishes "quotes updated", every open SSE connection receives it.

Single-process by design; a multi-instance version would swap this for Postgres LISTEN/NOTIFY.
Events carry no user data, so the stream needs no auth and no token appears in a URL;
clients refetch the authenticated /state on each tick.
"""
from __future__ import annotations

import asyncio
from typing import Any

_subscribers: set[asyncio.Queue] = set()
QUEUE_SIZE = 100
MAX_SUBSCRIBERS = 500  # a single process should not accept unbounded open streams


class TooManySubscribers(Exception):
    pass


def subscribe() -> asyncio.Queue:
    if len(_subscribers) >= MAX_SUBSCRIBERS:
        raise TooManySubscribers(len(_subscribers))
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: dict[str, Any]) -> int:
    """Non-blocking fan-out: a slow consumer's full queue drops the tick rather than stalling the poller."""
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
