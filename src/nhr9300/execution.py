"""Reusable workflow lifecycle: validation, preflight, execution and cleanup."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .acquisition import AcquisitionCollector
from .arm_lease import ArmLeaseSupervisor
from .backends.ivi import IVIBackend
from .backends.simulator import SimulatedBackend
from .evidence import atomic_write_json
from .instrument import NHR9300
from .interlocks import StaticInterlockProvider
from .profiles import load_workflow_profile, validate_workflow_profile
from .qualification import (
    require_disabled_inactive,
    require_safe_start,
    safety_limit_mismatches,
)
from .sequences import SequenceRunner
from .types import OperatingState, RoutineState, Setpoints, to_jsonable


@dataclass(frozen=True, slots=True)
class WorkflowRequest:
    profile: Path
    output: Path
    hardware: bool = False
    resource: str | None = None
    preflight_only: bool = False
    acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class WorkflowOutcome:
    passed: bool
    report_path: Path


def _zeroed(status: Any) -> bool:
    values = status.setpoints
    return (
        values.voltage == values.current == values.power == values.resistance == 0.0
        and not values.voltage_enabled
        and not values.current_enabled
        and not values.power_enabled
        and not values.resistance_enabled
    )


def _require_final_safe(status: Any, watchdog: bool) -> None:
    require_disabled_inactive(status)
    if not _zeroed(status):
        raise RuntimeError("Final setpoints or channels are not zeroed")
    if watchdog:
        raise RuntimeError("Watchdog remained enabled after cleanup")


def _write_report(path: Path, report: dict[str, Any]) -> None:
    atomic_write_json(path, report)


def _write_artifact_manifest(
    *, report_path: Path, report: dict[str, Any], instrument_id: str
) -> Path:
    """Describe durable files by role so operators need not infer filenames."""
    artifacts: list[dict[str, Any]] = []

    def add(role: str, raw_path: str | Path, **extra: Any) -> None:
        path = Path(raw_path).resolve()
        if not path.is_file():
            return
        content = path.read_bytes()
        artifacts.append(
            {
                "role": role,
                "path": str(path),
                "instrument_id": instrument_id,
                "run_id": report["run_id"],
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                **extra,
            }
        )

    add("report", report_path)
    add("workflow_profile", report["profile_file"])
    for dynamic in report.get("dynamic_profile_files", []):
        add("workflow_dynamic_profile", dynamic["path"], stage=dynamic["stage"])
    sequence = report.get("sequence_result", {})
    if sequence.get("global_csv_path"):
        add("workflow_sequence", sequence["global_csv_path"])
    for index, stage in enumerate(sequence.get("stages", [])):
        if stage.get("csv_path"):
            add("workflow_stage", stage["csv_path"], stage_index=index)
    manifest_path = report_path.with_name("artifacts.json")
    atomic_write_json(
        manifest_path,
        {
            "schema_version": 1,
            "instrument_id": instrument_id,
            "run_id": report["run_id"],
            "preflight": bool(report.get("preflight_only")),
            "arm_lease": report.get("arm_lease", {"enabled": False}),
            "artifacts": artifacts,
        },
    )
    return manifest_path


def _dynamic_profile_evidence(configuration: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for stage in configuration.stages:
        if stage.type != "csv_profile":
            continue
        content = stage.csv_path.read_bytes()
        evidence.append(
            {
                "stage": stage.name,
                "path": str(stage.csv_path.resolve()),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    return evidence


def execute_workflow_on_runtime(
    *,
    profile: Path,
    output: Path,
    run_id: str,
    instrument: NHR9300,
    collector: AcquisitionCollector,
    hardware: bool,
    preflight_only: bool = False,
    stop_event: threading.Event | None = None,
    progress_callback: Callable[[int, Any, str | None], None] | None = None,
    reconnect_after_cleanup: bool = False,
    workflow_id: str | None = None,
    bundle_digest: str | None = None,
    simulator_initial_state_policy: str | None = None,
    external_interlock_evidence: Callable[[], Mapping[str, Any]] | None = None,
    runtime_safety_failure: Callable[[Exception], bool] | None = None,
    arm_lease_supervisor: ArmLeaseSupervisor | None = None,
) -> WorkflowOutcome:
    """Execute an approved workflow on a service-owned runtime.

    The caller owns the instrument and acquisition lifecycles. This function
    never creates a backend and never closes the runtime permanently.
    """
    stop_event = stop_event or threading.Event()
    try:
        configuration = load_workflow_profile(profile)
        validate_workflow_profile(configuration, hardware=hardware)
        if configuration.external_interlocks and external_interlock_evidence is None:
            raise ValueError(
                "External interlock workflows require the service-owned snapshot runtime"
            )
        dynamic_profile_files = _dynamic_profile_evidence(configuration)
        planned_stages = (
            None
            if preflight_only
            else configuration.sequence(configure_limits=False)
        )
        if dynamic_profile_files != _dynamic_profile_evidence(configuration):
            raise ValueError("A dynamic profile CSV changed while it was being loaded")
    except (OSError, ValueError) as exc:
        raise ValueError(f"Workflow profile rejected: {exc}") from exc

    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    profile_bytes = profile.read_bytes()
    report: dict[str, Any] = {
        "schema_version": 1,
        "workflow": "nhr_workflow",
        "run_id": run_id,
        "workflow_id": workflow_id,
        "bundle_digest": bundle_digest,
        "execution": "hardware" if hardware else "simulation",
        "preflight_only": preflight_only,
        "resource": configuration.expected_resource,
        "profile_file": str(profile.resolve()),
        "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "dynamic_profile_files": dynamic_profile_files,
        "configuration": to_jsonable(configuration),
        "started_at_utc": datetime.now(timezone.utc),
        "passed": False,
    }
    cleanup_authorized = False
    limits_configured = False
    collector_was_running = collector.running

    try:
        if not hardware and simulator_initial_state_policy == "reset_from_profile":
            if collector.running:
                collector.stop()
            if instrument.connected:
                instrument.close()
            instrument.reset_simulator_initial_state(
                configuration.simulation_initial_voltage_v
            )
            report["simulator_initial_state"] = {
                "policy": simulator_initial_state_policy,
                "configured_voltage_v": configuration.simulation_initial_voltage_v,
                "actual_initial_voltage_v": configuration.simulation_initial_voltage_v,
            }
        instrument.connect()
        collector.start()
        identity = instrument.read_identity()
        report["identity"] = to_jsonable(identity)
        if hardware:
            if identity.logical_name != configuration.expected_resource:
                raise RuntimeError(
                    "Connected resource does not match expected_resource"
                )
            if identity.serial_number != configuration.expected_serial_number:
                raise RuntimeError(
                    "Connected serial number does not match expected_serial_number"
                )
        initial_status = instrument.read_status()
        report["initial_status"] = to_jsonable(initial_status)
        require_safe_start(initial_status)
        initial_watchdog = instrument.read_watchdog()
        report["initial_watchdog"] = initial_watchdog
        if initial_watchdog:
            raise RuntimeError(
                "The workflow runner requires the watchdog disabled initially"
            )
        instrument.read_measurement()
        instrument.check_interlocks()
        cleanup_authorized = True

        instrument.configure_safety_limits(configuration.safety_limits)
        limits_configured = True
        readback = instrument.read_safety_limits()
        mismatches = safety_limit_mismatches(configuration.safety_limits, readback)
        report["safety_limits_requested"] = to_jsonable(configuration.safety_limits)
        report["safety_limits_readback"] = to_jsonable(readback)
        report["safety_limit_mismatches"] = mismatches
        if mismatches:
            raise RuntimeError(
                "Safety-limit readback differs from the approved profile: "
                + "; ".join(mismatches)
            )

        if preflight_only:
            report["preflight_passed"] = True
        else:
            if configuration.workflow_limits.arm_lease_renewal_enabled:
                if arm_lease_supervisor is None:
                    raise RuntimeError(
                        "Arm lease renewal requires a service-owned workflow run"
                    )
                arm_lease_supervisor.start_sequence()
            if configuration.watchdog_enabled:
                instrument.set_watchdog(True)
                if not instrument.read_watchdog():
                    raise RuntimeError("Watchdog enable was not read back")
            if planned_stages is None:
                raise RuntimeError("Sequence stages were not prepared")
            sequence_result = SequenceRunner(
                instrument,
                collector,
                manage_collector=False,
                stop_event=stop_event,
                progress_callback=progress_callback,
                failure_handler=runtime_safety_failure,
                arm_lease_supervisor=arm_lease_supervisor,
            ).run(planned_stages)
            report["sequence_result"] = to_jsonable(sequence_result)
            if sequence_result.state == RoutineState.STOPPED and stop_event.is_set():
                report["stopped"] = True
                report["stop_reason"] = sequence_result.reason
            elif sequence_result.state != RoutineState.PASSED:
                raise RuntimeError(sequence_result.reason)
    except Exception as exc:
        handled = False
        if runtime_safety_failure is not None:
            try:
                handled = runtime_safety_failure(exc)
            except Exception:
                handled = False
        if handled:
            report["stopped"] = True
            report["stop_reason"] = f"{type(exc).__name__}: {exc}"
        else:
            report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if arm_lease_supervisor is not None:
            try:
                arm_lease_supervisor.close(
                    "workflow_cleanup_after_"
                    + ("error" if "error" in report else "completion")
                )
                report["arm_lease"] = arm_lease_supervisor.snapshot()
            except Exception as exc:
                report["arm_lease_evidence_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
                report.setdefault(
                    "error", "Arm lease evidence could not be finalized"
                )
        if cleanup_authorized:
            failures: list[str] = []
            emergency_used = False
            if "error" in report:
                try:
                    instrument.emergency_stop("Workflow failure cleanup")
                    emergency_used = True
                except Exception as exc:
                    failures.append(f"emergency_stop: {type(exc).__name__}: {exc}")
            operations: list[tuple[str, Callable[[], None]]] = []
            if limits_configured:
                operations.append(
                    (
                        "zero_setpoints",
                        lambda: instrument.configure_setpoints(
                            Setpoints(state=OperatingState.STANDBY)
                        ),
                    )
                )
            operations.extend(
                (
                    ("disable", instrument.disable),
                    ("disable_watchdog", lambda: instrument.set_watchdog(False)),
                )
            )
            for name, operation in operations:
                try:
                    operation()
                except Exception as exc:
                    failures.append(f"{name}: {type(exc).__name__}: {exc}")
            if failures and not emergency_used:
                try:
                    instrument.emergency_stop("Controlled workflow cleanup failed")
                    emergency_used = True
                except Exception as exc:
                    failures.append(
                        f"emergency_fallback: {type(exc).__name__}: {exc}"
                    )
            report["emergency_fallback_used"] = emergency_used
            try:
                if failures:
                    raise RuntimeError("; ".join(failures))
                cleanup_status = instrument.read_status()
                cleanup_watchdog = instrument.read_watchdog()
                _require_final_safe(cleanup_status, cleanup_watchdog)
                report["cleanup_status"] = to_jsonable(cleanup_status)
                report["cleanup_watchdog"] = cleanup_watchdog
                if reconnect_after_cleanup:
                    collector.stop()
                    try:
                        instrument.close()
                        instrument.connect()
                        final_status = instrument.read_status()
                        final_watchdog = instrument.read_watchdog()
                        _require_final_safe(final_status, final_watchdog)
                        report["status_after_reconnect"] = to_jsonable(final_status)
                        report["watchdog_after_reconnect"] = final_watchdog
                    finally:
                        if instrument.connected:
                            collector.start()
                report["final_safe_state_verified"] = True
            except Exception as exc:
                report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
                report["final_safe_state_verified"] = False
        else:
            report["cleanup_skipped"] = "Initial safe-start gate did not pass"
            report["final_safe_state_verified"] = False
        if external_interlock_evidence is not None:
            try:
                interlock_evidence = dict(external_interlock_evidence())
                report["external_interlocks"] = interlock_evidence
                if interlock_evidence.get("stop_cause") is not None:
                    report["stop_cause"] = interlock_evidence["stop_cause"]
            except Exception as exc:
                report["external_interlock_evidence_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
        report["passed"] = (
            "error" not in report
            and not report.get("stopped", False)
            and report.get("final_safe_state_verified", False)
        )
        report["ended_at_utc"] = datetime.now(timezone.utc)
        report["outcome"] = (
            "passed"
            if report["passed"]
            else "stopped"
            if report.get("stopped")
            else "failed"
        )
        report["artifact_manifest_path"] = str(
            report_path.with_name("artifacts.json").resolve()
        )
        _write_report(report_path, report)
        _write_artifact_manifest(
            report_path=report_path,
            report=report,
            instrument_id=instrument.instrument_id,
        )
        if not collector_was_running and collector.running:
            collector.stop()
    return WorkflowOutcome(bool(report["passed"]), report_path.resolve())


def execute_workflow(request: WorkflowRequest) -> WorkflowOutcome:
    hardware = request.hardware
    if hardware and not request.resource:
        raise ValueError("A hardware workflow requires a resource")
    if hardware and not request.acknowledged:
        raise ValueError("A hardware workflow requires explicit acknowledgement")
    try:
        configuration = load_workflow_profile(request.profile)
        validate_workflow_profile(configuration, hardware=hardware)
        if configuration.workflow_limits.arm_lease_renewal_enabled:
            raise ValueError(
                "Arm lease renewal is available only through the 32-bit service"
            )
        if configuration.external_interlocks:
            raise ValueError(
                "External interlock workflows require the service-owned snapshot runtime"
            )
        dynamic_profile_files = _dynamic_profile_evidence(configuration)
        planned_stages = (
            None
            if request.preflight_only
            else configuration.sequence(configure_limits=False)
        )
        if dynamic_profile_files != _dynamic_profile_evidence(configuration):
            raise ValueError("A dynamic profile CSV changed while it was being loaded")
    except (OSError, ValueError) as exc:
        raise ValueError(f"Workflow profile rejected: {exc}") from exc
    if hardware and request.resource != configuration.expected_resource:
        raise ValueError("resource does not match expected_resource in the approved profile")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = request.output / run_id
    output.mkdir(parents=True, exist_ok=False)
    report_path = output / "report.json"
    profile_bytes = request.profile.read_bytes()
    if hardware:
        backend = IVIBackend("hardware", request.resource)
        instrument_id = "hardware"
    else:
        backend = SimulatedBackend(
            "workflow-sim",
            initial_voltage_v=configuration.simulation_initial_voltage_v,
        )
        instrument_id = "workflow-sim"
    instrument = NHR9300(
        instrument_id,
        backend,
        interlocks=[StaticInterlockProvider(safe=True, name="operator_supervised")],
    )
    collector = AcquisitionCollector(
        instrument,
        rate_hz=10.0,
        csv_path=output / "measurements.csv",
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "workflow": "nhr_workflow",
        "run_id": run_id,
        "execution": "hardware" if hardware else "simulation",
        "preflight_only": request.preflight_only,
        "resource": request.resource if hardware else instrument_id,
        "profile_file": str(request.profile.resolve()),
        "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "dynamic_profile_files": dynamic_profile_files,
        "configuration": to_jsonable(configuration),
        "started_at_utc": datetime.now(timezone.utc),
        "passed": False,
    }
    cleanup_authorized = False
    limits_configured = False

    try:
        instrument.connect()
        identity = instrument.read_identity()
        report["identity"] = to_jsonable(identity)
        if hardware and identity.serial_number != configuration.expected_serial_number:
            raise RuntimeError("Connected serial number does not match expected_serial_number")
        initial_status = instrument.read_status()
        report["initial_status"] = to_jsonable(initial_status)
        require_safe_start(initial_status)
        initial_watchdog = instrument.read_watchdog()
        report["initial_watchdog"] = initial_watchdog
        if initial_watchdog:
            raise RuntimeError("The workflow runner requires the watchdog disabled initially")
        cleanup_authorized = True

        instrument.configure_safety_limits(configuration.safety_limits)
        limits_configured = True
        readback = instrument.read_safety_limits()
        mismatches = safety_limit_mismatches(configuration.safety_limits, readback)
        report["safety_limits_requested"] = to_jsonable(configuration.safety_limits)
        report["safety_limits_readback"] = to_jsonable(readback)
        report["safety_limit_mismatches"] = mismatches
        if mismatches:
            raise RuntimeError("Safety-limit readback differs from the approved profile: " + "; ".join(mismatches))

        if request.preflight_only:
            report["preflight_passed"] = True
        else:
            if configuration.watchdog_enabled:
                instrument.set_watchdog(True)
                if not instrument.read_watchdog():
                    raise RuntimeError("Watchdog enable was not read back")
            if planned_stages is None:
                raise RuntimeError("Sequence stages were not prepared")
            sequence_result = SequenceRunner(instrument, collector).run(planned_stages)
            report["sequence_result"] = to_jsonable(sequence_result)
            if sequence_result.state != RoutineState.PASSED:
                raise RuntimeError(sequence_result.reason)
    except KeyboardInterrupt:
        report["error"] = "KeyboardInterrupt: operator requested stop"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if cleanup_authorized:
            try:
                failures: list[str] = []
                operations = [
                    ("emergency_stop", lambda: instrument.emergency_stop("Workflow final cleanup")),
                ]
                if limits_configured:
                    operations.append(
                        ("zero_setpoints", lambda: instrument.configure_setpoints(Setpoints(state=OperatingState.STANDBY)))
                    )
                operations.extend(
                    (("disable", instrument.disable), ("disable_watchdog", lambda: instrument.set_watchdog(False)))
                )
                for name, operation in operations:
                    try:
                        operation()
                    except Exception as exc:
                        failures.append(f"{name}: {type(exc).__name__}: {exc}")
                if failures:
                    raise RuntimeError("; ".join(failures))
                cleanup_status = instrument.read_status()
                cleanup_watchdog = instrument.read_watchdog()
                _require_final_safe(cleanup_status, cleanup_watchdog)
                report["cleanup_status"] = to_jsonable(cleanup_status)
                report["cleanup_watchdog"] = cleanup_watchdog
                instrument.close()
                instrument.connect()
                final_status = instrument.read_status()
                final_watchdog = instrument.read_watchdog()
                _require_final_safe(final_status, final_watchdog)
                report["status_after_reconnect"] = to_jsonable(final_status)
                report["watchdog_after_reconnect"] = final_watchdog
                report["passed"] = "error" not in report
            except Exception as exc:
                report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
                report["passed"] = False
        else:
            report["cleanup_skipped"] = "Initial safe-start gate did not pass"
        try:
            instrument.close()
        except Exception as exc:
            report["close_error"] = f"{type(exc).__name__}: {exc}"
            report["passed"] = False
        report["ended_at_utc"] = datetime.now(timezone.utc)
        report["outcome"] = "passed" if report["passed"] else "failed"
        report["artifact_manifest_path"] = str(
            report_path.with_name("artifacts.json").resolve()
        )
        _write_report(report_path, report)
        _write_artifact_manifest(
            report_path=report_path,
            report=report,
            instrument_id=instrument.instrument_id,
        )
    return WorkflowOutcome(bool(report["passed"]), report_path.resolve())
