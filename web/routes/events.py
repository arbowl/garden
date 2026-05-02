import asyncio
import json
import logging

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from db.connection import get_db
from db.queries import get_activity_status
from web.broadcaster import broadcaster

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/events")
async def event_stream(request: Request):
    q = broadcaster.subscribe()

    async def stream():
        # Send current activity state immediately on connect
        try:
            db = await get_db()
            status = await get_activity_status(db)
            yield {"event": "activity", "data": json.dumps(status)}
        except Exception:
            pass

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25)
                    yield {"event": event["type"], "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            broadcaster.unsubscribe(q)

    return EventSourceResponse(stream())
