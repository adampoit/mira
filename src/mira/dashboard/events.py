"""Server-Sent Events (SSE) for real-time dashboard updates.

Webhook handlers emit events via `events.emit(...)`, connected clients
receive them over the `/api/events` endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Event:
    type: str
    data: dict[str, object]


class EventBus:
    """In-memory pub-sub for dashboard events. One queue per subscriber."""

    _subscribers: set[asyncio.Queue[Event]]
    _lock: asyncio.Lock

    def __init__(self) -> None:
        self._subscribers = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    def emit(self, event_type: str, data: Mapping[str, object] | None = None) -> None:
        """Broadcast an event to all subscribers. Safe to call from any thread."""
        event = Event(type=event_type, data=dict(data or {}))
        # Use create_task so sync callers don't need await
        try:
            loop = asyncio.get_running_loop()
            _ = loop.create_task(self._broadcast(event))
        except RuntimeError:
            # No running loop (called from sync context) — best effort
            pass

    async def _broadcast(self, event: Event) -> None:
        async with self._lock:
            dead: list[asyncio.Queue[Event]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("Dropping event for slow subscriber")
                except Exception:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)


# Module-level singleton
bus = EventBus()


def format_sse(event: Event) -> str:
    """Format an Event as an SSE message."""
    return f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"
