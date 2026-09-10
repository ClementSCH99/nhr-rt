"""Composable safety interlocks, including future CAN/BMS providers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol, Sequence

from .errors import NHRInterlockError
from .types import InterlockSignal


class InterlockProvider(Protocol):
    def signals(self) -> Sequence[InterlockSignal]:
        """Return the provider's latest safety signals."""


class StaticInterlockProvider:
    """Simple provider useful for supervised benches and tests."""

    def __init__(self, safe: bool = True, name: str = "operator_supervision") -> None:
        self.safe = safe
        self.name = name
        self.detail = ""

    def signals(self) -> Sequence[InterlockSignal]:
        return [InterlockSignal(self.name, self.safe, time.monotonic(), self.detail)]


class CompositeInterlockProvider:
    """Combine independent providers without weakening fail-closed validation."""

    def __init__(self, providers: Sequence[InterlockProvider]) -> None:
        self.providers = tuple(providers)

    def signals(self) -> Sequence[InterlockSignal]:
        return [signal for provider in self.providers for signal in provider.signals()]


class CallbackInterlockProvider:
    """Adapt an external signal snapshot callback to the interlock protocol.

    The callback owns transport-specific decoding and timestamps. Empty,
    unsafe, or stale snapshots are rejected later by :func:`validate_interlocks`.
    """

    def __init__(self, callback: Callable[[], Sequence[InterlockSignal]]) -> None:
        self.callback = callback

    def signals(self) -> Sequence[InterlockSignal]:
        return tuple(self.callback())


def validate_interlocks(
    providers: Sequence[InterlockProvider],
    max_age_s: float,
    now: float | None = None,
) -> list[InterlockSignal]:
    checked_at = time.monotonic() if now is None else now
    signals = [signal for provider in providers for signal in provider.signals()]
    if not signals:
        raise NHRInterlockError("At least one interlock signal is required")
    failed = [
        signal
        for signal in signals
        if not signal.safe
        or checked_at - signal.timestamp_monotonic
        > (signal.max_age_s if signal.max_age_s is not None else max_age_s)
    ]
    if failed:
        details = ", ".join(
            f"{signal.name} ({signal.detail or ('unsafe' if not signal.safe else 'stale')})"
            for signal in failed
        )
        raise NHRInterlockError(f"Unsafe or stale interlocks: {details}")
    return signals
