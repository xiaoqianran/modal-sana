from __future__ import annotations

import queue
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Event:
    type: str
    job_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "job_id": self.job_id,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
        }


class EventBus:
    """In-process pub/sub used by the CLI progress bar and Web SSE."""

    def __init__(self, history: int = 2000) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list[queue.Queue[Event]]] = defaultdict(list)
        self._history: dict[str, list[Event]] = defaultdict(list)
        self._history_limit = history

    def publish(self, event: Event) -> None:
        with self._lock:
            bucket = self._history[event.job_id]
            bucket.append(event)
            if len(bucket) > self._history_limit:
                del bucket[: len(bucket) - self._history_limit]
            subscribers = list(self._subs.get(event.job_id, []))
            subscribers.extend(self._subs.get("*", []))
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                pass

    def subscribe(self, job_id: str = "*", maxsize: int = 256) -> queue.Queue[Event]:
        subscriber: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs[job_id].append(subscriber)
        return subscriber

    def unsubscribe(self, job_id: str, subscriber: queue.Queue[Event]) -> None:
        with self._lock:
            listeners = self._subs.get(job_id, [])
            if subscriber in listeners:
                listeners.remove(subscriber)

    def history(self, job_id: str) -> list[Event]:
        with self._lock:
            return list(self._history.get(job_id, []))
