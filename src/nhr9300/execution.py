"""Reusable workflow lifecycle: validation, preflight, execution and cleanup."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .acquisition import AcquisitionCollector
from .backends.ivi import IVIBackend
from .backends.simulator import SimulatedBackend
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
    path.write_text(
        json.dumps(to_jsonable(report), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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


def execute_workflow(request: WorkflowRequest) -> WorkflowOutcome:
    hardware = request.hardware
    if hardware and not request.resource:
        raise ValueError("A hardware workflow requires a resource")
    if hardware and not request.acknowledged:
        raise ValueError("A hardware workflow requires explicit acknowledgement")
    try:
        configuration = load_workflow_profile(request.profile)
        validate_workflow_profile(configuration, hardware=hardware)
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
        _write_report(report_path, report)
    return WorkflowOutcome(bool(report["passed"]), report_path.resolve())
