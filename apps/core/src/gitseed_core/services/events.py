from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from gitseed_core.db import get_redis

REDIS_EVENTS_CHANNEL = "gitseed:events"
_PROCESS_ORIGIN_ID = uuid.uuid4().hex

_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()


def _build_event_envelope(
    *,
    type: str,
    actor_type: str = "system",
    actor_id: str | None = None,
    project_id: str | None = None,
    seed_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_iso = datetime.now(UTC).isoformat()
    return {
        "id": uuid.uuid4().hex[:12],
        "origin": _PROCESS_ORIGIN_ID,
        "type": str(type),
        "actor_type": str(actor_type),
        "actor_id": actor_id,
        "project_id": project_id,
        "seed_id": seed_id,
        "data": data or {},
        "timestamp": now_iso,
        "occurred_at": now_iso,
    }


def _fanout_local(envelope: dict[str, Any]) -> None:
    """Push event to all active in-process SSE subscriber queues without blocking."""
    for queue in list(_subscribers):
        try:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(envelope)
        except Exception:
            pass


async def _publish_redis(envelope: dict[str, Any]) -> None:
    """Best-effort publish to Valkey/Redis Pub/Sub for multi-worker fanout."""
    try:
        redis = get_redis()
        await redis.publish(REDIS_EVENTS_CHANNEL, json.dumps(envelope, default=str))
    except Exception:
        # Silently ignore if Redis is unavailable (e.g. unit tests)
        pass


async def run_redis_events_listener(stop_event: asyncio.Event | None = None) -> None:
    """Subscribe to Redis Pub/Sub and fan out remote events to local SSE subscribers."""
    while stop_event is None or not stop_event.is_set():
        pubsub = None
        try:
            redis = get_redis()
            if not hasattr(redis, "pubsub"):
                return
            pubsub = redis.pubsub()
            await pubsub.subscribe(REDIS_EVENTS_CHANNEL)
            while stop_event is None or not stop_event.is_set():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg and msg.get("type") == "message":
                    raw_data = msg.get("data")
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8", errors="ignore")
                    if isinstance(raw_data, str):
                        envelope = json.loads(raw_data)
                        if (
                            isinstance(envelope, dict)
                            and envelope.get("origin") != _PROCESS_ORIGIN_ID
                        ):
                            _fanout_local(envelope)
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                raise
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(REDIS_EVENTS_CHANNEL)
                    await pubsub.close()
                except Exception:
                    pass


def emit_from_sync(
    *,
    type: str,
    actor_type: str = "system",
    actor_id: str | None = None,
    project_id: str | None = None,
    seed_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit a real-time SSE event from synchronous or asynchronous service code."""
    envelope = _build_event_envelope(
        type=type,
        actor_type=actor_type,
        actor_id=actor_id,
        project_id=project_id,
        seed_id=seed_id,
        data=data,
    )
    _fanout_local(envelope)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_publish_redis(envelope))
        from gitseed_core.services.push_relay import dispatch_push_relay

        loop.create_task(dispatch_push_relay(envelope))
    except RuntimeError:
        pass
    return envelope


def subscribe_events() -> AsyncIterator[dict[str, Any]]:
    """Subscribe to real-time control plane events yielded as dictionaries."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
    _subscribers.add(queue)

    async def _stream() -> AsyncIterator[dict[str, Any]]:
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            _subscribers.discard(queue)

    return _stream()


def format_sse_message(
    event_data: dict[str, Any],
    *,
    event_name: str = "gitseed_event",
) -> str:
    """Format an event envelope as a standard Server-Sent Events (SSE) frame."""
    event_id = event_data.get("id") or ""
    payload = json.dumps(event_data, default=str)
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    if event_name:
        lines.append(f"event: {event_name}")
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"

