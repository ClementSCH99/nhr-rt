"""Service-owned external snapshots and typed fail-closed interlock rules."""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from .errors import NHRInterlockError, NHRValidationError
from .types import InterlockSignal


SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
COMPARISONS = frozenset({"minimum", "maximum", "range", "equals"})
APPLIES = frozenset({"pre_start", "runtime", "both"})
MAX_EXTERNAL_SOURCES = 32
MAX_SIGNALS_PER_SNAPSHOT = 256


@dataclass(frozen=True, slots=True)
class ExternalInterlockRule:
    rule_id: str
    source_id: str
    signal: str
    comparison: str
    unit: str
    applies: str
    max_age_s: float
    stability_duration_s: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    expected: bool | None = None


@dataclass(frozen=True, slots=True)
class ExternalSnapshot:
    source_id: str
    sequence: int
    timestamp_utc: datetime
    received_at_utc: datetime
    received_monotonic: float
    data_monotonic: float
    health: str
    signals: Mapping[str, float | bool]


class ExternalInterlockManager:
    """Validate snapshots and expose active workflow rules as interlock signals."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshots: dict[str, ExternalSnapshot] = {}
        self._source_faults: dict[str, str] = {}
        self._rules: tuple[ExternalInterlockRule, ...] = ()
        self._phase = "idle"
        self._latch_runtime = False
        self._latched: dict[str, InterlockSignal] = {}
        self._safe_since: dict[str, float] = {}

    @staticmethod
    def _validate_source_id(source_id: str) -> None:
        if not SOURCE_ID_PATTERN.fullmatch(source_id):
            raise NHRValidationError(
                "source_id must contain 1-64 letters, digits, '.', '_' or '-'"
            )

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise NHRValidationError("timestamp_utc must be a non-empty ISO-8601 string")
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise NHRValidationError("timestamp_utc must be valid ISO-8601") from exc
        if parsed.tzinfo is None:
            raise NHRValidationError("timestamp_utc must include a UTC offset")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _parse_signals(value: Any) -> dict[str, float | bool]:
        if not isinstance(value, Mapping) or not value:
            raise NHRValidationError("signals must be a non-empty object")
        if len(value) > MAX_SIGNALS_PER_SNAPSHOT:
            raise NHRValidationError(
                f"signals cannot contain more than {MAX_SIGNALS_PER_SNAPSHOT} entries"
            )
        signals: dict[str, float | bool] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if not name:
                raise NHRValidationError("signal names must be non-empty")
            if isinstance(raw_value, bool):
                signals[name] = raw_value
            elif isinstance(raw_value, (int, float)) and math.isfinite(float(raw_value)):
                signals[name] = float(raw_value)
            else:
                raise NHRValidationError(
                    f"signal {name!r} must be a finite number or boolean"
                )
        return signals

    def submit(self, source_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Store one strictly newer snapshot and return its public representation."""
        self._validate_source_id(source_id)
        allowed = {"sequence", "timestamp_utc", "health", "signals"}
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        try:
            with self._lock:
                tracked_sources = set(self._snapshots) | set(self._source_faults)
                if (
                    source_id not in tracked_sources
                    and len(tracked_sources) >= MAX_EXTERNAL_SOURCES
                ):
                    raise NHRValidationError(
                        f"external source limit of {MAX_EXTERNAL_SOURCES} reached"
                    )
            if unknown:
                raise NHRValidationError(f"Unknown snapshot fields: {sorted(unknown)}")
            if missing:
                raise NHRValidationError(f"Missing snapshot fields: {sorted(missing)}")
            sequence = payload["sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                raise NHRValidationError("sequence must be a non-negative integer")
            timestamp_utc = self._parse_timestamp(payload["timestamp_utc"])
            health = payload["health"]
            if not isinstance(health, str) or not health.strip():
                raise NHRValidationError("health must be a non-empty string")
            health = health.strip().lower()
            signals = self._parse_signals(payload["signals"])
            received_at_utc = datetime.now(timezone.utc)
            transport_age_s = (received_at_utc - timestamp_utc).total_seconds()
            if transport_age_s < -1.0:
                raise NHRValidationError("timestamp_utc is more than 1 s in the future")
            received_monotonic = time.monotonic()
            data_monotonic = received_monotonic - max(0.0, transport_age_s)
            snapshot = ExternalSnapshot(
                source_id=source_id,
                sequence=sequence,
                timestamp_utc=timestamp_utc,
                received_at_utc=received_at_utc,
                received_monotonic=received_monotonic,
                data_monotonic=data_monotonic,
                health=health,
                signals=signals,
            )
            with self._lock:
                prior = self._snapshots.get(source_id)
                if prior is not None and sequence <= prior.sequence:
                    raise NHRValidationError(
                        f"sequence must increase for source {source_id!r}; "
                        f"last accepted value is {prior.sequence}"
                    )
                for rule in self._rules:
                    if rule.source_id != source_id:
                        continue
                    value = snapshot.signals.get(rule.signal)
                    continuity_lost = (
                        prior is not None
                        and (
                            snapshot.received_monotonic - prior.received_monotonic
                            > rule.max_age_s
                            or snapshot.data_monotonic - prior.data_monotonic
                            > rule.max_age_s
                        )
                    )
                    if (
                        continuity_lost
                        or snapshot.health != "ok"
                        or value is None
                        or not self._condition(rule, value)
                    ):
                        self._safe_since.pop(rule.rule_id, None)
                self._snapshots[source_id] = snapshot
                self._source_faults.pop(source_id, None)
            return self._snapshot_public(snapshot)
        except NHRValidationError as exc:
            with self._lock:
                tracked_sources = set(self._snapshots) | set(self._source_faults)
                if (
                    source_id in tracked_sources
                    or len(tracked_sources) < MAX_EXTERNAL_SOURCES
                ):
                    self._source_faults[source_id] = str(exc)
            raise

    def activate(
        self,
        rules: Sequence[ExternalInterlockRule],
        *,
        phase: str = "pre_start",
        latch_runtime: bool = False,
    ) -> None:
        with self._lock:
            self._rules = tuple(rules)
            self._phase = phase
            self._latch_runtime = latch_runtime
            self._latched.clear()
            self._safe_since.clear()

    def set_phase(self, phase: str) -> None:
        if phase not in {"pre_start", "runtime"}:
            raise ValueError(f"Unsupported external interlock phase: {phase}")
        with self._lock:
            self._phase = phase

    def deactivate(self) -> None:
        with self._lock:
            self._rules = ()
            self._phase = "idle"
            self._latch_runtime = False
            self._latched.clear()
            self._safe_since.clear()

    @staticmethod
    def _applies(rule: ExternalInterlockRule, phase: str) -> bool:
        return rule.applies == "both" or rule.applies == phase

    @staticmethod
    def _condition(rule: ExternalInterlockRule, value: float | bool) -> bool:
        if rule.comparison == "equals":
            return isinstance(value, bool) and value is rule.expected
        if isinstance(value, bool):
            return False
        numeric = float(value)
        if rule.comparison == "minimum":
            return rule.minimum is not None and numeric >= rule.minimum
        if rule.comparison == "maximum":
            return rule.maximum is not None and numeric <= rule.maximum
        return (
            rule.minimum is not None
            and rule.maximum is not None
            and rule.minimum <= numeric <= rule.maximum
        )

    def _evaluate(self, rule: ExternalInterlockRule, now: float) -> InterlockSignal:
        snapshot = self._snapshots.get(rule.source_id)
        fault = self._source_faults.get(rule.source_id)
        value: float | bool | None = None
        timestamp = 0.0
        source_sequence: int | None = None
        source_timestamp_utc: datetime | None = None
        source_received_at_utc: datetime | None = None
        source_age_s_at_evaluation: float | None = None
        reason: str | None = None
        safe = False
        if fault:
            reason = f"source_rejected: {fault}"
        elif snapshot is None:
            reason = "source_missing"
        else:
            timestamp = snapshot.data_monotonic
            source_sequence = snapshot.sequence
            source_timestamp_utc = snapshot.timestamp_utc
            source_received_at_utc = snapshot.received_at_utc
            value = snapshot.signals.get(rule.signal)
            age_s = max(0.0, now - snapshot.data_monotonic)
            source_age_s_at_evaluation = age_s
            if snapshot.health != "ok":
                reason = f"source_health_{snapshot.health}"
            elif value is None:
                reason = "signal_missing"
            elif age_s > rule.max_age_s:
                reason = "signal_stale"
            elif not self._condition(rule, value):
                reason = "condition_violated"
                self._safe_since.pop(rule.rule_id, None)
            else:
                since = self._safe_since.setdefault(
                    rule.rule_id, snapshot.received_monotonic
                )
                if now - since < rule.stability_duration_s:
                    reason = "stabilizing"
                else:
                    safe = True
        detail = "ok" if safe else str(reason)
        return InterlockSignal(
            name=rule.rule_id,
            safe=safe,
            timestamp_monotonic=timestamp,
            detail=detail,
            max_age_s=rule.max_age_s,
            source_id=rule.source_id,
            signal=rule.signal,
            value=value,
            unit=rule.unit,
            reason=reason,
            source_sequence=source_sequence,
            source_timestamp_utc=source_timestamp_utc,
            source_received_at_utc=source_received_at_utc,
            source_age_s_at_evaluation=source_age_s_at_evaluation,
        )

    def signals(self) -> Sequence[InterlockSignal]:
        with self._lock:
            now = time.monotonic()
            results: list[InterlockSignal] = []
            for rule in self._rules:
                if not self._applies(rule, self._phase):
                    continue
                latched = self._latched.get(rule.rule_id)
                result = latched or self._evaluate(rule, now)
                if (
                    not result.safe
                    and self._phase == "runtime"
                    and self._latch_runtime
                ):
                    self._latched[rule.rule_id] = result
                results.append(result)
            return tuple(results)

    def require_safe(self) -> None:
        failed = [signal for signal in self.signals() if not signal.safe]
        if failed:
            details = ", ".join(
                f"{signal.name} ({signal.reason or signal.detail})"
                for signal in failed
            )
            raise NHRInterlockError(f"Unsafe external interlocks: {details}")

    @staticmethod
    def _snapshot_public(snapshot: ExternalSnapshot) -> dict[str, Any]:
        return {
            "source_id": snapshot.source_id,
            "sequence": snapshot.sequence,
            "timestamp_utc": snapshot.timestamp_utc,
            "received_at_utc": snapshot.received_at_utc,
            "health": snapshot.health,
            "signals": dict(snapshot.signals),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            sources = []
            source_ids = sorted(set(self._snapshots) | set(self._source_faults))
            for source_id in source_ids:
                snapshot = self._snapshots.get(source_id)
                sources.append(
                    {
                        "source_id": source_id,
                        "sequence": None if snapshot is None else snapshot.sequence,
                        "timestamp_utc": None if snapshot is None else snapshot.timestamp_utc,
                        "received_at_utc": (
                            None if snapshot is None else snapshot.received_at_utc
                        ),
                        "age_s": (
                            None
                            if snapshot is None
                            else max(0.0, now - snapshot.data_monotonic)
                        ),
                        "health": None if snapshot is None else snapshot.health,
                        "last_rejection": self._source_faults.get(source_id),
                    }
                )
            results = [self._result_public(item) for item in self.signals()]
            return {
                "status": "configured" if self._rules else "inactive",
                "phase": self._phase,
                "sources": sources,
                "results": results,
                "latched": bool(self._latched),
            }

    @staticmethod
    def _result_public(signal: InterlockSignal) -> dict[str, Any]:
        return {
            "rule_id": signal.name,
            "source_id": signal.source_id,
            "signal": signal.signal,
            "safe": signal.safe,
            "value": signal.value,
            "unit": signal.unit,
            "reason": signal.reason,
            "max_age_s": signal.max_age_s,
            "detail": signal.detail,
            "source_sequence": signal.source_sequence,
            "source_timestamp_utc": signal.source_timestamp_utc,
            "source_received_at_utc": signal.source_received_at_utc,
            "source_age_s_at_evaluation": signal.source_age_s_at_evaluation,
        }


__all__ = [
    "APPLIES",
    "COMPARISONS",
    "MAX_EXTERNAL_SOURCES",
    "MAX_SIGNALS_PER_SNAPSHOT",
    "SOURCE_ID_PATTERN",
    "ExternalInterlockManager",
    "ExternalInterlockRule",
    "ExternalSnapshot",
]
