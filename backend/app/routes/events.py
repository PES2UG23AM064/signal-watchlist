"""Server-Sent Events: push "quotes updated" ticks so clients refresh the moment the poller writes,
instead of polling on a timer. Carries no user data (no auth, no token in the URL); the client refetches
the authenticated /state on each tick. Polling remains as the fallback transport."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import events

router = APIRouter(tags=["events"])
KEEPALIVE_S = 15


async def _stream(q):
    try:
        yield "retry: 5000\n\n"
        yield f"data: {json.dumps({'type': 'hello', 'subscribers': events.subscriber_count()})}\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
                yield f"data: {json.dumps(ev)}\n\n"
            except TimeoutError:
                yield ": keepalive\n\n"  # comment frame keeps proxies/browsers from closing an idle stream
    finally:
        events.unsubscribe(q)


@router.get("/events")
async def sse() -> StreamingResponse:
    try:
        q = events.subscribe()  # bounded: a single process refuses unbounded open streams
    except events.TooManySubscribers as e:
        raise HTTPException(503, "too many open streams; polling fallback applies") from e
    return StreamingResponse(_stream(q), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
