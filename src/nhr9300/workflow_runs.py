"""Service-owned asynchronous execution of approved workflow bundles."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .acquisition import AcquisitionCollector, AcquisitionSample
from .arm_lease import (
    ArmLeaseExpiredError,
    ArmLeaseRenewalError,
    ArmLeaseSupervisor,
)
from .errors import (
    NHRInterlockError,
    NHRNotArmedError,
    NHRPolicyError,
    NHRStateError,
    NHRValidationError,
)
from .external_interlocks import ExternalInterlockManager
from .evidence import atomic_write_json
from .execution import execute_workflow_on_runtime
from .instrument import NHR9300
from .routines import RoutineRunner
from .types import to_jsonable
from .workflow_registry import WorkflowRegistry, WorkflowRegistryEntry


WORKFLOW_ACKNOWLEDGEMENT = "SUPERVISED_WORKFLOW_READY"
TERMINAL_STATES = {"passed", "stopped", "failed", "interrupted"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowRunController:
    """Own one instrument's workflow reservation, thread and durable state."""

    def __init__(
        self,
        *,
        instrument: NHR9300,
        collector: AcquisitionCollector,
        legacy_runner: RoutineRunner,
        registry: WorkflowRegistry,
        output_dir: Path,
        hardware: bool,
        remote_enabled: bool,
        operation_lock: threading.RLock,
        controlled_stop_timeout_s: float | None,
        external_interlocks: ExternalInterlockManager,
        reconnect_after_cleanup: bool = True,
        simulator_initial_state_policy: str | None = None,
    ) -> None:
        self.instrument = instrument
        self.collector = collector
        self.legacy_runner = legacy_runner
        self.registry = registry
        self.output_dir = output_dir / "workflow-runs"
        self.hardware = hardware
        self.remote_enabled = remote_enabled
        self.operation_lock = operation_lock
        self.controlled_stop_timeout_s = controlled_stop_timeout_s
        self.external_interlocks = external_interlocks
        self.reconnect_after_cleanup = reconnect_after_cleanup
        self.simulator_initial_state_policy = simulator_initial_state_policy
        self._state_lock = threading.RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._request_ids: dict[str, str] = {}
        self._active_run_id: str | None = None
        self._preflight_active = False
        self._preflight_done = threading.Event()
        self._preflight_done.set()
        self._threads: dict[str, threading.Thread] = {}
        self._stop_monitors: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._arm_supervisors: dict[str, ArmLeaseSupervisor] = {}
        self._recovery_required = False
        self._recover_manifests()

    def _run_dir(self, run_id: str) -> Path:
        return self.output_dir / run_id

    def _manifest_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run-state.json"

    def _persist(self, run_id: str) -> None:
        path = self._manifest_path(run_id)
        atomic_write_json(path, self._public(self._runs[run_id]))

    def _recover_manifests(self) -> None:
        if not self.output_dir.exists():
            return
        for path in self.output_dir.glob("*/run-state.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            run_id = str(state.get("run_id", path.parent.name))
            if state.get("instrument_id") != self.instrument.instrument_id:
                continue
            if state.get("state") not in TERMINAL_STATES:
                state["state"] = "interrupted"
                state["outcome"] = "interrupted"
                state["ended_at_utc"] = _utc_now()
                state["final_safe_state"] = {
                    "verified": False,
                    "reason": "Service process ended before terminal persistence",
                }
                self._recovery_required = True
                self._runs[run_id] = state
                self._persist(run_id)
            elif not state.get("final_safe_state", {}).get("verified", False):
                self._recovery_required = True
            self._runs[run_id] = state
            request_id = state.get("request_id")
            if request_id:
                self._request_ids[str(request_id)] = run_id

    def _require_enabled(self) -> None:
        if not self.remote_enabled:
            raise NHRPolicyError(
                "Remote approved-workflow control is disabled for this instrument"
            )

    def _require_available(self) -> None:
        if (
            self._active_run_id is not None
            or self._preflight_active
            or self.legacy_runner.running
        ):
            raise NHRStateError("The instrument already has an active operation")

    @staticmethod
    def _public(run: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in run.items() if not key.startswith("_")}

    def workflows(self) -> list[dict[str, Any]]:
        return self.registry.list_for(self.instrument.instrument_id)

    @property
    def active(self) -> bool:
        with self._state_lock:
            return self._active_run_id is not None or self._preflight_active

    def preflight(self, workflow_id: str, bundle_digest: str) -> dict[str, Any]:
        """Run synchronous safety/identity checks, but never workflow stages."""
        self._require_enabled()
        entry = self.registry.require(
            self.instrument.instrument_id, workflow_id, bundle_digest
        )
        with self._state_lock:
            self._require_available()
            self._preflight_active = True
            self._preflight_done.clear()
        preflight_id = f"preflight-{_utc_now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        target = self.output_dir.parent / "workflow-preflights" / preflight_id
        rules = entry.bundle.configuration.external_interlocks  # type: ignore[union-attr]
        self.external_interlocks.activate(rules, phase="pre_start")
        try:
            with self.operation_lock:
                self.instrument.connect()
                self.collector.start()
                profile = entry.bundle.materialize(target / "bundle")  # type: ignore[union-attr]
                outcome = execute_workflow_on_runtime(
                    profile=profile,
                    output=target,
                    run_id=preflight_id,
                    instrument=self.instrument,
                    collector=self.collector,
                    hardware=self.hardware,
                    preflight_only=True,
                    reconnect_after_cleanup=self.reconnect_after_cleanup,
                    workflow_id=workflow_id,
                    bundle_digest=bundle_digest,
                    simulator_initial_state_policy=self.simulator_initial_state_policy,
                    external_interlock_evidence=self.external_interlocks.status,
                )
        finally:
            self.external_interlocks.deactivate()
            with self._state_lock:
                self._preflight_active = False
                self._preflight_done.set()
        report = json.loads(outcome.report_path.read_text(encoding="utf-8"))
        with self._state_lock:
            self._recovery_required = not report.get(
                "final_safe_state_verified", False
            )
        return {
            "preflight_id": preflight_id,
            "workflow_id": workflow_id,
            "bundle_digest": bundle_digest,
            "passed": outcome.passed,
            "report_path": str(outcome.report_path),
            "checks": {
                "identity": report.get("identity"),
                "initial_status": report.get("initial_status"),
                "initial_watchdog": report.get("initial_watchdog"),
                "safety_limit_mismatches": report.get("safety_limit_mismatches"),
                "final_safe_state_verified": report.get(
                    "final_safe_state_verified", False
                ),
                "external_interlocks": report.get("external_interlocks"),
            },
            "error": report.get("error") or report.get("cleanup_error"),
        }

    def start(
        self,
        *,
        request_id: str,
        workflow_id: str,
        bundle_digest: str,
        operator_acknowledgement: str,
    ) -> tuple[dict[str, Any], bool]:
        """Reserve the instrument and start a registered workflow thread.

        The boolean is true only when this call created the run; an idempotent
        retry returns the existing snapshot and false.
        """
        self._require_enabled()
        if self.hardware:
            if operator_acknowledgement != WORKFLOW_ACKNOWLEDGEMENT:
                raise NHRPolicyError(
                    "Hardware workflow requires per-run operator acknowledgement"
                )
            if self.controlled_stop_timeout_s is None:
                raise NHRPolicyError(
                    "Hardware workflow requires an explicitly approved controlled-stop timeout"
                )
        try:
            uuid.UUID(request_id)
        except ValueError as exc:
            raise NHRValidationError("request_id must be a UUID") from exc
        with self._state_lock:
            prior_id = self._request_ids.get(request_id)
            if prior_id is not None:
                prior = self._runs[prior_id]
                if (
                    prior["workflow_id"] != workflow_id
                    or prior["bundle_digest"] != bundle_digest
                ):
                    raise NHRStateError(
                        "request_id was already used for a different workflow request"
                    )
                return self._public(prior), False
        entry = self.registry.require(
            self.instrument.instrument_id, workflow_id, bundle_digest
        )
        with self._state_lock:
            prior_id = self._request_ids.get(request_id)
            if prior_id is not None:
                prior = self._runs[prior_id]
                if (
                    prior["workflow_id"] != workflow_id
                    or prior["bundle_digest"] != bundle_digest
                ):
                    raise NHRStateError(
                        "request_id was already used for a different workflow request"
                    )
                return self._public(prior), False
            if self._recovery_required:
                raise NHRPolicyError(
                    "An interrupted run requires a successful preflight before restart"
                )
            self._require_available()
            rules = entry.bundle.configuration.external_interlocks  # type: ignore[union-attr]
            self.external_interlocks.activate(
                rules, phase="pre_start", latch_runtime=True
            )
            try:
                self.external_interlocks.require_safe()
            except Exception:
                self.external_interlocks.deactivate()
                raise
            self.external_interlocks.set_phase("runtime")
            run_id = str(uuid.uuid4())
            run = {
                "run_id": run_id,
                "request_id": request_id,
                "workflow_id": workflow_id,
                "bundle_digest": bundle_digest,
                "instrument_id": self.instrument.instrument_id,
                "state": "accepted",
                "outcome": None,
                "accepted_at_utc": _utc_now(),
                "started_at_utc": None,
                "ended_at_utc": None,
                "stage": None,
                "step": None,
                "termination": None,
                "progress": {"percent": None},
                "totals": {
                    "capacity_charge_ah": 0.0,
                    "capacity_discharge_ah": 0.0,
                    "energy_charge_wh": 0.0,
                    "energy_discharge_wh": 0.0,
                },
                "stop_requested": False,
                "stop_cause": None,
                "emergency_fallback_requested": False,
                "arm_lease": {"enabled": False},
                "report_path": str((self._run_dir(run_id) / "report.json").resolve()),
                "final_safe_state": {"verified": False},
                "error": None,
                "_counter_previous": {},
            }
            self._runs[run_id] = run
            self._request_ids[request_id] = run_id
            self._active_run_id = run_id
            stop_event = threading.Event()
            self._stops[run_id] = stop_event
            self._persist(run_id)
            thread = threading.Thread(
                target=self._execute,
                args=(run_id, entry, stop_event),
                name=f"workflow-{self.instrument.instrument_id}-{run_id[:8]}",
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()
            return self._public(run), True

    def _update(self, run_id: str, **values: Any) -> None:
        with self._state_lock:
            self._runs[run_id].update(values)
            self._persist(run_id)

    def _progress(self, run_id: str, entry: WorkflowRegistryEntry, index: int, stage: Any, step: str | None) -> None:
        profile = entry.bundle.configuration.stages[index]  # type: ignore[union-attr]
        termination = None
        if profile.termination is not None:
            termination = to_jsonable(profile.termination)
        elif profile.type == "cccv":
            termination = {
                "field": "cutoff_current",
                "operator": "<=",
                "value": profile.cutoff_current_a,
                "relative": False,
            }
        with self._state_lock:
            run = self._runs[run_id]
            prior_stage = run.get("stage")
            if prior_stage is None or prior_stage.get("index") != index:
                run["_stage_started_monotonic"] = time.monotonic()
                latest = self.collector.latest
                if latest is not None:
                    measurement = latest.measurement
                    run["_termination_baseline"] = {
                        "capacity_charge_ah": measurement.capacity_charge_ah,
                        "capacity_discharge_ah": measurement.capacity_discharge_ah,
                        "energy_charge_wh": (
                            None
                            if measurement.energy_charge_kwh is None
                            else measurement.energy_charge_kwh * 1000.0
                        ),
                        "energy_discharge_wh": (
                            None
                            if measurement.energy_discharge_kwh is None
                            else measurement.energy_discharge_kwh * 1000.0
                        ),
                    }
            if not run["stop_requested"]:
                run["state"] = "running"
            run["stage"] = {
                "index": index,
                "count": len(entry.bundle.configuration.stages),  # type: ignore[union-attr]
                "name": stage.name,
                "type": profile.type,
                "duration_s": profile.duration_s,
            }
            run["step"] = step
            run["termination"] = termination
            self._persist(run_id)

    def _execute(
        self,
        run_id: str,
        entry: WorkflowRegistryEntry,
        stop_event: threading.Event,
    ) -> None:
        with self._state_lock:
            requested = self._runs[run_id]["stop_requested"]
        self._update(
            run_id,
            state="stop_requested" if requested else "preflighting",
            started_at_utc=_utc_now(),
        )
        callback = lambda sample: self._accumulate(run_id, sample)
        self.collector.add_callback(callback)
        supervisor: ArmLeaseSupervisor | None = None
        try:
            with self.operation_lock:
                entry.bundle.verify_unchanged()  # type: ignore[union-attr]
                target = self._run_dir(run_id)
                profile = entry.bundle.materialize(target / "bundle")  # type: ignore[union-attr]
                configuration = entry.bundle.configuration  # type: ignore[union-attr]
                if configuration.workflow_limits.arm_lease_renewal_enabled:
                    supervisor = ArmLeaseSupervisor(
                        run_id=run_id,
                        workflow_id=entry.workflow_id,
                        bundle_digest=entry.bundle.digest,  # type: ignore[union-attr]
                        instrument=self.instrument,
                        collector=self.collector,
                        safety_limits=configuration.safety_limits,
                        max_sequence_duration_s=(
                            configuration.workflow_limits.max_sequence_duration_s
                        ),
                        stop_event=stop_event,
                        event_callback=lambda snapshot: self._arm_lease_update(
                            run_id, snapshot
                        ),
                        deadline_callback=lambda reason: self._duration_limit_stop(
                            run_id, reason
                        ),
                    )
                    with self._state_lock:
                        self._arm_supervisors[run_id] = supervisor
                outcome = execute_workflow_on_runtime(
                    profile=profile,
                    output=target,
                    run_id=run_id,
                    instrument=self.instrument,
                    collector=self.collector,
                    hardware=self.hardware,
                    stop_event=stop_event,
                    progress_callback=lambda index, stage, step: self._progress(
                        run_id, entry, index, stage, step
                    ),
                    reconnect_after_cleanup=self.reconnect_after_cleanup,
                    workflow_id=entry.workflow_id,
                    bundle_digest=entry.bundle.digest,  # type: ignore[union-attr]
                    simulator_initial_state_policy=self.simulator_initial_state_policy,
                    external_interlock_evidence=lambda: self._interlock_evidence(
                        run_id
                    ),
                    runtime_safety_failure=self.handle_runtime_failure,
                    arm_lease_supervisor=supervisor,
                )
            report = json.loads(outcome.report_path.read_text(encoding="utf-8"))
            sequence = report.get("sequence_result", {})
            if report.get("stopped"):
                state = outcome_name = "stopped"
            elif outcome.passed:
                state = outcome_name = "passed"
            else:
                state = outcome_name = "failed"
            totals = {
                name: sequence.get(name, 0.0)
                for name in (
                    "capacity_charge_ah",
                    "capacity_discharge_ah",
                    "energy_charge_wh",
                    "energy_discharge_wh",
                )
            }
            final_verified = report.get("final_safe_state_verified", False)
            if not final_verified:
                self._recovery_required = True
            self._update(
                run_id,
                state=state,
                outcome=outcome_name,
                ended_at_utc=_utc_now(),
                totals=totals,
                final_safe_state={
                    "verified": final_verified,
                    "reconnected": "status_after_reconnect" in report,
                },
                error=report.get("error") or report.get("cleanup_error"),
            )
        except Exception as exc:
            self._recovery_required = True
            try:
                self.instrument.emergency_stop("Workflow controller failure")
            except Exception as stop_exc:
                detail = f"{exc}; emergency stop failed: {stop_exc}"
            else:
                detail = str(exc)
            self._update(
                run_id,
                state="failed",
                outcome="failed",
                ended_at_utc=_utc_now(),
                error=f"{type(exc).__name__}: {detail}",
                final_safe_state={"verified": False},
            )
        finally:
            self.collector.remove_callback(callback)
            self.external_interlocks.deactivate()
            with self._state_lock:
                self._arm_supervisors.pop(run_id, None)
                if self._active_run_id == run_id:
                    self._active_run_id = None

    def _arm_lease_update(self, run_id: str, snapshot: Mapping[str, Any]) -> None:
        """Persist renewal evidence in the service-owned runtime manifest."""
        with self._state_lock:
            run = self._runs.get(run_id)
            if run is None:
                raise NHRStateError("Arm lease event has no active workflow run")
            run["arm_lease"] = dict(snapshot)
            self._persist(run_id)

    def _duration_limit_stop(self, run_id: str, reason: str) -> None:
        """Route an approved stage/sequence overrun through controlled stop."""
        try:
            self.stop(
                run_id,
                origin="approved_duration_limit",
                cause={"reason": reason},
            )
        except NHRValidationError:
            return

    def get(self, run_id: str) -> dict[str, Any]:
        with self._state_lock:
            try:
                run = self._runs[run_id]
            except KeyError as exc:
                raise NHRValidationError(f"Unknown workflow run: {run_id}") from exc
            result = self._public(run)
            stage = result.get("stage")
            started = run.get("_stage_started_monotonic")
            if stage is not None and started is not None and run["state"] not in TERMINAL_STATES:
                elapsed = max(0.0, time.monotonic() - started)
                if result.get("termination") is not None:
                    result["termination_metric"] = self._termination_progress(run)
                raw_duration = stage.get("duration_s")
                if raw_duration is not None:
                    duration = float(raw_duration)
                    result["progress"] = {
                        "kind": "elapsed_time",
                        "current": elapsed,
                        "target": duration,
                        "unit": "s",
                        "percent": min(100.0, elapsed / duration * 100.0),
                    }
                elif result.get("termination") is not None:
                    result["progress"] = result["termination_metric"]
            return result

    def _termination_progress(self, run: Mapping[str, Any]) -> dict[str, Any]:
        termination = run["termination"]
        field = termination["field"]
        target = termination["value"]
        current: float | None = None
        unit = {
            "voltage": "V",
            "temperature": "degC",
            "capacity_ah": "Ah",
            "energy_wh": "Wh",
            "cutoff_current": "A",
        }.get(field)
        latest = self.collector.latest
        if latest is not None:
            measurement = latest.measurement
            if field == "voltage":
                current = measurement.voltage_v
            elif field == "temperature":
                current = measurement.temperature_c
            elif field == "cutoff_current":
                current = abs(measurement.current_a)
            elif field in {"capacity_ah", "energy_wh"}:
                suffix = "capacity" if field == "capacity_ah" else "energy"
                baseline = run.get("_termination_baseline", {})
                raw_values = (
                    (
                        measurement.capacity_charge_ah,
                        measurement.capacity_discharge_ah,
                    )
                    if field == "capacity_ah"
                    else (
                        None
                        if measurement.energy_charge_kwh is None
                        else measurement.energy_charge_kwh * 1000.0,
                        None
                        if measurement.energy_discharge_kwh is None
                        else measurement.energy_discharge_kwh * 1000.0,
                    )
                )
                names = (f"{suffix}_charge_{'ah' if suffix == 'capacity' else 'wh'}", f"{suffix}_discharge_{'ah' if suffix == 'capacity' else 'wh'}")
                deltas = [
                    max(0.0, abs(float(value)) - abs(float(baseline[name])))
                    for name, value in zip(names, raw_values)
                    if value is not None and baseline.get(name) is not None
                ]
                current = max(deltas) if deltas else None
        return {
            "kind": "termination_metric",
            "field": field,
            "operator": termination["operator"],
            "current": current,
            "target": target,
            "unit": unit,
            "percent": None,
        }

    def runtime_snapshot(self) -> dict[str, Any]:
        """Return the active run, or an explicit idle state, for observability."""
        with self._state_lock:
            run_id = self._active_run_id
        if run_id is None:
            return {
                "active": False,
                "state": "idle",
                "progress": None,
                "progress_available": False,
            }
        result = self.get(run_id)
        result["active"] = result.get("state") not in TERMINAL_STATES
        result["progress_available"] = result.get("progress") is not None
        return result

    def stop(
        self,
        run_id: str,
        *,
        origin: str = "operator",
        cause: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Set cooperative cancellation and return whether it was newly accepted."""
        with self._state_lock:
            run = self._runs.get(run_id)
            if run is None:
                raise NHRValidationError(f"Unknown workflow run: {run_id}")
            if run["state"] in TERMINAL_STATES:
                return self._public(run), False
            first_request = not run["stop_requested"]
            run["stop_requested"] = True
            run["state"] = "stop_requested"
            if first_request:
                run["stop_cause"] = {
                    "origin": origin,
                    "requested_at_utc": _utc_now(),
                    "detail": dict(cause or {}),
                }
            self._stops[run_id].set()
            self._persist(run_id)
            if first_request and self.controlled_stop_timeout_s is not None:
                monitor = threading.Thread(
                    target=self._enforce_stop_timeout,
                    args=(run_id, self.controlled_stop_timeout_s),
                    name=f"workflow-stop-{run_id[:8]}",
                    daemon=True,
                )
                self._stop_monitors[run_id] = monitor
                monitor.start()
            return self._public(run), first_request

    def _enforce_stop_timeout(self, run_id: str, timeout_s: float) -> None:
        thread = self._threads[run_id]
        thread.join(timeout_s)
        if not thread.is_alive():
            return
        with self._state_lock:
            run = self._runs[run_id]
            if run["state"] in TERMINAL_STATES:
                return
            run["emergency_fallback_requested"] = True
            self._persist(run_id)
        try:
            self.instrument.emergency_stop(
                "Controlled workflow stop exceeded its approved timeout"
            )
        except Exception as exc:
            with self._state_lock:
                self._runs[run_id]["error"] = (
                    "Emergency stop fallback failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                self._persist(run_id)

    def _interlock_evidence(self, run_id: str) -> dict[str, Any]:
        with self._state_lock:
            run = self._runs.get(run_id, {})
            stop_cause = run.get("stop_cause")
            emergency_fallback_requested = run.get(
                "emergency_fallback_requested", False
            )
        return {
            **self.external_interlocks.status(),
            "stop_cause": stop_cause,
            "emergency_fallback_requested": emergency_fallback_requested,
        }

    def handle_runtime_failure(self, error: Exception) -> bool:
        """Route external interlock faults through controlled workflow stop."""
        if isinstance(error, (ArmLeaseExpiredError, NHRNotArmedError)):
            with self._state_lock:
                run_id = self._active_run_id
                supervisor = (
                    None if run_id is None else self._arm_supervisors.get(run_id)
                )
            if run_id is None:
                return False
            if supervisor is not None:
                supervisor.record_external_expiration(str(error))
            try:
                self.instrument.emergency_stop(
                    "Arm lease expired during approved workflow"
                )
            finally:
                with self._state_lock:
                    self._runs[run_id]["emergency_fallback_requested"] = True
                self.stop(
                    run_id,
                    origin="arm_lease_expired",
                    cause={"error": f"{type(error).__name__}: {error}"},
                )
            return True
        if isinstance(error, ArmLeaseRenewalError):
            with self._state_lock:
                run_id = self._active_run_id
            if run_id is None:
                return False
            _, requested = self.stop(
                run_id,
                origin="arm_lease_renewal",
                cause={"error": f"{type(error).__name__}: {error}"},
            )
            return requested or self._stops[run_id].is_set()
        if not isinstance(error, NHRInterlockError):
            return False
        status = self.external_interlocks.status()
        if not status["latched"]:
            return False
        with self._state_lock:
            run_id = self._active_run_id
        if run_id is None:
            return False
        _, requested = self.stop(
            run_id,
            origin="external_interlock",
            cause={"results": status["results"], "sources": status["sources"]},
        )
        return requested or self._stops[run_id].is_set()

    def _accumulate(self, run_id: str, sample: AcquisitionSample) -> None:
        measurement = sample.measurement
        values = {
            "capacity_charge_ah": measurement.capacity_charge_ah,
            "capacity_discharge_ah": measurement.capacity_discharge_ah,
            "energy_charge_wh": (
                None
                if measurement.energy_charge_kwh is None
                else measurement.energy_charge_kwh * 1000.0
            ),
            "energy_discharge_wh": (
                None
                if measurement.energy_discharge_kwh is None
                else measurement.energy_discharge_kwh * 1000.0
            ),
        }
        with self._state_lock:
            run = self._runs.get(run_id)
            if run is None or run["state"] in TERMINAL_STATES:
                return
            previous = run["_counter_previous"]
            for name, raw in values.items():
                if raw is None:
                    continue
                current = abs(float(raw))
                if name in previous:
                    delta = (
                        current - previous[name]
                        if current >= previous[name]
                        else current
                    )
                    run["totals"][name] += max(0.0, delta)
                previous[name] = current

    def close(self, fallback_timeout_s: float) -> None:
        with self._state_lock:
            run_id = self._active_run_id
            preflight_active = self._preflight_active
        if preflight_active and not self._preflight_done.wait(fallback_timeout_s):
            raise NHRStateError(
                "Active workflow preflight did not finish during service shutdown"
            )
        if run_id is None:
            return
        self.stop(run_id)
        timeout = self.controlled_stop_timeout_s
        if timeout is None:
            timeout = fallback_timeout_s
        thread = self._threads[run_id]
        thread.join(timeout)
        if thread.is_alive():
            self.instrument.emergency_stop("Workflow service shutdown fallback")
            thread.join(fallback_timeout_s)
        if thread.is_alive():
            raise NHRStateError("Active workflow did not stop during service shutdown")


__all__ = ["WORKFLOW_ACKNOWLEDGEMENT", "WorkflowRunController"]
