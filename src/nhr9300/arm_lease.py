"""Service-owned, bounded arm-lease renewal for approved workflows."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .acquisition import AcquisitionCollector
from .errors import NHRStateError
from .instrument import NHR9300
from .qualification import safety_limit_mismatches
from .types import Measurement, OperatingState, SafetyLimits, Setpoints, to_jsonable


ARM_LEASE_DURATION_S = 300.0
ARM_RENEWAL_MARGIN_S = 60.0
DEADLINE_DETECTION_GRACE_S = 0.2
ACTIVE_STAGE_TYPES = frozenset(
    {"constant_current", "cccv", "constant_power", "csv_profile"}
)


class ArmLeaseRenewalError(NHRStateError):
    """A service-owned renewal was refused or could not be completed safely."""


class ArmLeaseExpiredError(ArmLeaseRenewalError):
    """The software arm lease expired while an approved stage was active."""


class ArmLeaseSupervisor:
    """Renew one workflow's lease without granting authority to its clients.

    The workflow loop calls :meth:`checkpoint` only after a new NHR measurement.
    Every successful renewal repeats the watchdog, acquisition, interlock,
    safety-limit and setpoint gates.  Stage and sequence deadlines are immutable.
    """

    def __init__(
        self,
        *,
        run_id: str,
        workflow_id: str | None,
        bundle_digest: str | None,
        instrument: NHR9300,
        collector: AcquisitionCollector,
        safety_limits: SafetyLimits,
        max_sequence_duration_s: float,
        stop_event: threading.Event,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        lease_duration_s: float = ARM_LEASE_DURATION_S,
        renewal_margin_s: float = ARM_RENEWAL_MARGIN_S,
        renewal_threshold_s: float = 295.0,
        deadline_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self.workflow_id = workflow_id
        self.bundle_digest = bundle_digest
        self.instrument = instrument
        self.collector = collector
        self.safety_limits = safety_limits
        self.max_sequence_duration_s = max_sequence_duration_s
        self.stop_event = stop_event
        self.event_callback = event_callback
        self.clock = clock
        self.lease_duration_s = lease_duration_s
        self.renewal_margin_s = renewal_margin_s
        self.renewal_threshold_s = renewal_threshold_s
        self.deadline_callback = deadline_callback
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._sequence_started: float | None = None
        self._sequence_deadline: float | None = None
        self._stage: dict[str, Any] | None = None
        self._expected_setpoints: Setpoints | None = None
        self._events: list[dict[str, Any]] = []
        self._renewal_count = 0
        self._status = "active"
        self._deadline_thread: threading.Thread | None = None
        self._deadline_reported: str | None = None

    def start_sequence(self) -> None:
        with self._lock:
            if self._sequence_started is not None:
                return
            self._sequence_started = self.clock()
            self._sequence_deadline = (
                self._sequence_started + self.max_sequence_duration_s
            )
            self._deadline_thread = threading.Thread(
                target=self._monitor_deadlines,
                name=f"arm-lease-deadline-{self.run_id[:8]}",
                daemon=True,
            )
            self._deadline_thread.start()

    def begin_stage(
        self,
        *,
        index: int,
        name: str,
        stage_type: str | None,
        duration_s: float | None,
    ) -> None:
        """Bind renewal authority to one immutable approved stage."""
        with self._lock:
            started = self.clock()
            self._stage = {
                "index": index,
                "name": name,
                "type": stage_type,
                "duration_s": duration_s,
                "started_monotonic": started,
                "deadline_monotonic": (
                    None if duration_s is None else started + duration_s
                ),
            }
            self._expected_setpoints = None
            self._deadline_reported = None
            if (
                stage_type in ACTIVE_STAGE_TYPES
                and duration_s is not None
                and duration_s > self.renewal_threshold_s
            ):
                self._record("stage_activated", reason="approved_active_stage")
            self._condition.notify_all()

    def end_stage(self) -> None:
        with self._lock:
            if self._renewal_required(self._stage):
                self._record("stage_completed", reason="stage_left_active_scope")
            self._stage = None
            self._expected_setpoints = None
            self._deadline_reported = None
            self._condition.notify_all()

    def close(self, reason: str) -> None:
        with self._lock:
            if self._status == "closed":
                return
            self._status = "closed"
            self._record("revoked", reason=reason)
            self._stage = None
            self._expected_setpoints = None
            self._condition.notify_all()
            thread = self._deadline_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def checkpoint(self, measurement: Measurement) -> None:
        """Renew near expiry, or fail closed when any safety gate is unavailable."""
        with self._lock:
            stage = self._stage
            if (
                self._status != "active"
                or stage is None
                or not self._renewal_required(stage)
            ):
                return
            if self.stop_event.is_set():
                return
            now = self.clock()
            if self._sequence_deadline is None:
                self._refuse("arm_lease_supervisor_not_started")
            if now >= self._sequence_deadline:
                return
            stage_deadline = stage.get("deadline_monotonic")
            if stage_deadline is not None and now >= stage_deadline:
                return

            status = self.instrument.read_status()
            armed_until = status.armed_until_monotonic
            if armed_until is None or now >= armed_until:
                self._record("expired", reason="arm_lease_expired")
                raise ArmLeaseExpiredError("Arm lease expired during approved workflow")

            if self._expected_setpoints is None:
                if status.state not in (
                    OperatingState.CHARGE,
                    OperatingState.DISCHARGE,
                ):
                    self._refuse("stage_not_in_approved_active_state")
                self._expected_setpoints = status.setpoints

            if armed_until - now > self.renewal_margin_s:
                return

            try:
                self._require_safe_renewal(measurement, status.setpoints)
                if self.stop_event.is_set():
                    self._refuse("stop_requested_during_renewal")
                remaining = min(
                    float(stage_deadline) - now if stage_deadline is not None else self.lease_duration_s,
                    self._sequence_deadline - now,
                )
                if remaining <= 0.0:
                    self._refuse("approved_duration_exceeded")
                duration = min(self.lease_duration_s, max(1.0, remaining + 5.0))
                previous_expiry = armed_until
                new_expiry = self.instrument.renew_arm(duration)
            except ArmLeaseExpiredError:
                raise
            except ArmLeaseRenewalError:
                raise
            except Exception as exc:
                self._record(
                    "refused",
                    reason=f"{type(exc).__name__}: {exc}",
                    previous_expiry_monotonic=armed_until,
                )
                raise ArmLeaseRenewalError(
                    f"Arm lease renewal failed closed: {type(exc).__name__}: {exc}"
                ) from exc

            self._renewal_count += 1
            self._record(
                "renewed",
                reason="all_service_owned_safety_gates_passed",
                previous_expiry_monotonic=previous_expiry,
                new_expiry_monotonic=new_expiry,
                lease_duration_s=duration,
                measurement_age_s=max(0.0, now - measurement.monotonic_s),
                watchdog_active=True,
            )
            if self.stop_event.is_set():
                self._record("revoked", reason="stop_requested_after_renewal")
                raise InterruptedError("Routine stop requested during arm renewal")

    def record_external_expiration(self, reason: str) -> None:
        with self._lock:
            self._record("expired", reason=reason)

    def _renewal_required(self, stage: dict[str, Any] | None) -> bool:
        return bool(
            stage is not None
            and stage.get("type") in ACTIVE_STAGE_TYPES
            and stage.get("duration_s") is not None
            and float(stage["duration_s"]) > self.renewal_threshold_s
        )

    def _monitor_deadlines(self) -> None:
        """Request controlled stop when an approved wall-clock bound is exceeded."""
        while True:
            callback_reason: str | None = None
            with self._condition:
                if self._status == "closed":
                    return
                now = self.clock()
                candidates: list[tuple[float, str]] = []
                if self._sequence_deadline is not None:
                    candidates.append(
                        (self._sequence_deadline, "approved_sequence_duration_exceeded")
                    )
                if self._stage is not None:
                    stage_deadline = self._stage.get("deadline_monotonic")
                    if stage_deadline is not None:
                        candidates.append(
                            (float(stage_deadline), "approved_stage_duration_exceeded")
                        )
                if not candidates:
                    self._condition.wait(timeout=0.2)
                    continue
                deadline, reason = min(candidates)
                remaining = deadline + DEADLINE_DETECTION_GRACE_S - now
                if remaining > 0:
                    self._condition.wait(timeout=min(remaining, 0.2))
                    continue
                if self._deadline_reported == reason:
                    self._condition.wait(timeout=0.2)
                    continue
                self._deadline_reported = reason
                self._record("refused", reason=reason)
                callback_reason = reason
            if callback_reason is not None and self.deadline_callback is not None:
                self.deadline_callback(callback_reason)

    def _require_safe_renewal(
        self, measurement: Measurement, current_setpoints: Setpoints
    ) -> None:
        if not self.collector.running:
            raise NHRStateError("Service-owned acquisition is not active")
        if self.collector.error:
            raise NHRStateError(
                f"Service-owned acquisition fault: {self.collector.error}"
            )
        now = self.clock()
        max_age = self.instrument.observability_state()["measurement_max_age_s"]
        if now - measurement.monotonic_s > max_age:
            raise NHRStateError("Latest NHR measurement is stale")
        if not self.instrument.read_watchdog():
            raise NHRStateError("NHR watchdog is not active")
        self.instrument.check_interlocks()
        if current_setpoints != self._expected_setpoints:
            raise NHRStateError(
                "NHR setpoints changed outside the approved stage"
            )
        mismatches = safety_limit_mismatches(
            self.safety_limits, self.instrument.read_safety_limits()
        )
        if mismatches:
            raise NHRStateError(
                "NHR safety limits changed: " + "; ".join(mismatches)
            )

    def _refuse(self, reason: str) -> None:
        self._record("refused", reason=reason)
        raise ArmLeaseRenewalError(f"Arm lease renewal refused: {reason}")

    def _record(self, decision: str, *, reason: str, **extra: Any) -> None:
        event = {
            "sequence": len(self._events) + 1,
            "timestamp_utc": datetime.now(timezone.utc),
            "monotonic_s": self.clock(),
            "decision": decision,
            "reason": reason,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "bundle_digest": self.bundle_digest,
            "stage": None if self._stage is None else dict(self._stage),
            **extra,
        }
        self._events.append(event)
        if self.event_callback is not None:
            self.event_callback(self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return to_jsonable(
                {
                    "enabled": True,
                    "status": self._status,
                    "lease_duration_s": self.lease_duration_s,
                    "renewal_margin_s": self.renewal_margin_s,
                    "max_sequence_duration_s": self.max_sequence_duration_s,
                    "sequence_deadline_monotonic": self._sequence_deadline,
                    "renewal_count": self._renewal_count,
                    "events": list(self._events),
                }
            )


__all__ = [
    "ACTIVE_STAGE_TYPES",
    "ARM_LEASE_DURATION_S",
    "ARM_RENEWAL_MARGIN_S",
    "DEADLINE_DETECTION_GRACE_S",
    "ArmLeaseExpiredError",
    "ArmLeaseRenewalError",
    "ArmLeaseSupervisor",
]
