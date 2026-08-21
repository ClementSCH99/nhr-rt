"""Read-only runtime snapshots and bounded event publication."""

from __future__ import annotations

import queue
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from collections.abc import Callable

from .types import to_jsonable


RUNTIME_SCHEMA_VERSION = "1.0"
EVENT_TYPES = frozenset({"measurement", "workflow", "interlock", "limit", "alert"})
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    schema_version: str
    sequence: int
    timestamp_utc: datetime
    instrument_id: str
    event: str
    data: Mapping[str, Any]


class RuntimeEventBroker:
    """Fan out display events without allowing viewers to block producers."""

    def __init__(self, instrument_id: str, subscriber_queue_size: int = 100) -> None:
        if subscriber_queue_size < 1:
            raise ValueError("subscriber_queue_size must be at least 1")
        self.instrument_id = instrument_id
        self.subscriber_queue_size = subscriber_queue_size
        self._lock = threading.Lock()
        self._subscribers: dict[queue.Queue[RuntimeEvent], int] = {}
        self._sequence = 0
        self._closed = threading.Event()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def subscribe(self) -> queue.Queue[RuntimeEvent]:
        target: queue.Queue[RuntimeEvent] = queue.Queue(
            maxsize=self.subscriber_queue_size
        )
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("Runtime event broker is closed")
            self._subscribers[target] = 0
        return target

    def unsubscribe(self, target: queue.Queue[RuntimeEvent]) -> None:
        with self._lock:
            self._subscribers.pop(target, None)

    def publish(self, event: str, data: Mapping[str, Any]) -> RuntimeEvent:
        if event not in EVENT_TYPES:
            raise ValueError(f"Unknown runtime event type: {event}")
        with self._lock:
            self._sequence += 1
            published = RuntimeEvent(
                schema_version=RUNTIME_SCHEMA_VERSION,
                sequence=self._sequence,
                timestamp_utc=datetime.now(timezone.utc),
                instrument_id=self.instrument_id,
                event=event,
                data=to_jsonable(dict(data)),
            )
            if self._closed.is_set():
                return published
            for target in tuple(self._subscribers):
                dropped = self._subscribers[target]
                try:
                    target.put_nowait(published)
                except queue.Full:
                    try:
                        target.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        dropped += 1
                    self._subscribers[target] = dropped
                    try:
                        target.put_nowait(published)
                    except queue.Full:
                        pass
        return published

    def take_dropped_count(self, target: queue.Queue[RuntimeEvent]) -> int:
        with self._lock:
            dropped = self._subscribers.get(target, 0)
            if target in self._subscribers:
                self._subscribers[target] = 0
            return dropped

    def close(self) -> None:
        self._closed.set()


class RuntimeEventPublisher:
    """Decouple acquisition callbacks from event-envelope construction."""

    def __init__(self, callback: Callable[[Any], None]) -> None:
        self._callback = callback
        self._pending: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="runtime-observability",
            daemon=True,
        )
        self._thread.start()

    def submit(self, value: Any) -> None:
        try:
            self._pending.put_nowait(value)
        except queue.Full:
            try:
                self._pending.get_nowait()
            except queue.Empty:
                pass
            try:
                self._pending.put_nowait(value)
            except queue.Full:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                value = self._pending.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._callback(value)
            except Exception:
                LOGGER.exception("Runtime observability publication failed")

    def close(self, timeout: float = 1.0) -> None:
        self._stop.set()
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("Runtime event publisher did not stop")


__all__ = [
    "EVENT_TYPES",
    "RUNTIME_SCHEMA_VERSION",
    "RuntimeEvent",
    "RuntimeEventBroker",
    "RuntimeEventPublisher",
]
