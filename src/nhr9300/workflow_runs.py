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
from .errors import NHRPolicyError, NHRStateError, NHRValidationError
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
        reconnect_after_cleanup: bool = True,
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
        self.reconnect_after_cleanup = reconnect_after_cleanup
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
        self._recovery_required = False
        self._recover_manifests()

    def _run_dir(self, run_id: str) -> Path:
        return self.output_dir / run_id

    def _manifest_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run-state.json"

    def _persist(self, run_id: str) -> None:
        path = self._manifest_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(to_jsonable(self._public(self._runs[run_id])), indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

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
                )
        finally:
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
                "emergency_fallback_requested": False,
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
        try:
            with self.operation_lock:
                entry.bundle.verify_unchanged()  # type: ignore[union-attr]
                target = self._run_dir(run_id)
                profile = entry.bundle.materialize(target / "bundle")  # type: ignore[union-attr]
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
            with self._state_lock:
                if self._active_run_id == run_id:
                    self._active_run_id = None

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
                duration = float(stage["duration_s"])
                result["progress"] = {
                    "kind": "elapsed_time",
                    "current": elapsed,
                    "target": duration,
                    "unit": "s",
                    "percent": min(100.0, elapsed / duration * 100.0),
                }
            return result

    def stop(self, run_id: str) -> tuple[dict[str, Any], bool]:
        with self._state_lock:
            run = self._runs.get(run_id)
            if run is None:
                raise NHRValidationError(f"Unknown workflow run: {run_id}")
            if run["state"] in TERMINAL_STATES:
                return self._public(run), False
            first_request = not run["stop_requested"]
            run["stop_requested"] = True
            run["state"] = "stop_requested"
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
