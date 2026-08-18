"""Run one supervised CC hold with durable evidence and safe cleanup."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nhr9300 import NHR9300, Setpoints, SimulatedBackend, StaticInterlockProvider
from nhr9300.acquisition import AcquisitionCollector
from nhr9300.backends.ivi import IVIBackend
from nhr9300.routines import RoutineRunner
from nhr9300.safety_validation import (
    require_disabled_inactive,
    require_safe_start,
    safety_limit_mismatches,
)
from nhr9300.cc_profiles import load_cc_profile, validate_cc_profile
from nhr9300.types import OperatingState, RoutineState, to_jsonable


WRITE_ACK = "SUPERVISED_CC_READY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one simulated or supervised constant-current hold."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate", action="store_true")
    mode.add_argument("--hardware", action="store_true")
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument(
        "--resource",
        default=os.environ.get("NHR9300_RESOURCE"),
        help="NHR logical resource; required for --hardware",
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("cc-results"))
    return parser.parse_args()


def _zeroed(status: Any) -> bool:
    setpoints = status.setpoints
    return (
        setpoints.voltage == 0.0
        and setpoints.current == 0.0
        and setpoints.power == 0.0
        and setpoints.resistance == 0.0
        and not setpoints.voltage_enabled
        and not setpoints.current_enabled
        and not setpoints.power_enabled
        and not setpoints.resistance_enabled
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


def _current_response(csv_path: Path, configuration: Any) -> dict[str, Any]:
    mode = configuration.cc_hold.mode
    expected = configuration.cc_hold.current_a
    if mode == OperatingState.DISCHARGE:
        expected = -expected
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    active = [
        row
        for row in rows
        if row["state"] == mode.name.lower() and row["step"].endswith("_wait")
    ]
    settling_time_s = configuration.cc_hold.current_settling_time_s
    first_active_monotonic_s = (
        float(active[0]["monotonic_s"]) if active else None
    )
    evaluated = (
        [
            row
            for row in active
            if float(row["monotonic_s"]) - first_active_monotonic_s
            >= settling_time_s
        ]
        if first_active_monotonic_s is not None
        else []
    )
    active_currents = [float(row["current_a"]) for row in active]
    currents = [float(row["current_a"]) for row in evaluated]
    response: dict[str, Any] = {
        "expected_current_a": expected,
        "tolerance_a": configuration.cc_hold.current_tolerance_a,
        "settling_time_s": settling_time_s,
        "minimum_active_samples": configuration.cc_hold.minimum_active_samples,
        "active_sample_count": len(active_currents),
        "transient_sample_count": len(active_currents) - len(currents),
        "evaluated_sample_count": len(currents),
        "passed": False,
    }
    if active_currents:
        response.update(
            {
                "first_active_current_a": active_currents[0],
                "active_minimum_current_a": min(active_currents),
                "active_maximum_current_a": max(active_currents),
                "active_maximum_absolute_error_a": max(
                    abs(current - expected) for current in active_currents
                ),
            }
        )
    if currents:
        response.update(
            {
                "mean_current_a": sum(currents) / len(currents),
                "minimum_current_a": min(currents),
                "maximum_current_a": max(currents),
                "maximum_absolute_error_a": max(
                    abs(current - expected) for current in currents
                ),
            }
        )
    response["passed"] = (
        len(currents) >= configuration.cc_hold.minimum_active_samples
        and all(
            abs(current - expected) <= configuration.cc_hold.current_tolerance_a
            for current in currents
        )
    )
    return response


def main() -> int:
    args = parse_args()
    hardware = bool(args.hardware)
    if hardware and not args.resource:
        raise SystemExit("Specify --resource or set NHR9300_RESOURCE.")
    if hardware and os.environ.get("NHR9300_CC_ACK") != WRITE_ACK:
        raise SystemExit(
            f"Set NHR9300_CC_ACK={WRITE_ACK} after the common bench preflight."
        )

    try:
        configuration = load_cc_profile(args.profile)
        validate_cc_profile(configuration, hardware=hardware)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"CC profile rejected: {exc}") from exc
    if hardware and args.resource != configuration.expected_resource:
        raise SystemExit(
            "--resource does not match expected_resource in the approved profile"
        )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output / run_id
    output.mkdir(parents=True, exist_ok=False)
    profile_bytes = args.profile.read_bytes()
    report_path = output / "report.json"

    if hardware:
        backend = IVIBackend("hardware", args.resource)
        instrument_id = "hardware"
    else:
        backend = SimulatedBackend(
            "cc-sim",
            initial_voltage_v=configuration.simulation_initial_voltage_v,
        )
        instrument_id = "cc-sim"
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
    if hardware:
        last_live_print = 0.0

        def print_live(sample: Any) -> None:
            nonlocal last_live_print
            now = time.monotonic()
            if now - last_live_print < 1.0:
                return
            last_live_print = now
            measurement = sample.measurement
            print(
                "LIVE "
                f"V={measurement.voltage_v:.3f} V "
                f"I={measurement.current_a:.3f} A "
                f"P={measurement.power_w:.1f} W "
                f"Q+={measurement.capacity_charge_ah} Ah "
                f"Q-={measurement.capacity_discharge_ah} Ah "
                f"E+={measurement.energy_charge_kwh} kWh "
                f"E-={measurement.energy_discharge_kwh} kWh",
                flush=True,
            )

        collector.add_callback(print_live)
    report: dict[str, Any] = {
        "workflow": "supervised_cc_hold",
        "run_id": run_id,
        "execution": "hardware" if hardware else "simulation",
        "preflight_only": bool(args.preflight_only),
        "resource": args.resource if hardware else instrument_id,
        "profile_file": str(args.profile.resolve()),
        "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
        "configuration": to_jsonable(configuration),
        "started_at_utc": datetime.now(timezone.utc),
        "passed": False,
    }
    cleanup_authorized = False
    limits_configured = False
    routine_result = None

    try:
        instrument.connect()
        identity = instrument.read_identity()
        report["identity"] = to_jsonable(identity)
        if hardware and identity.serial_number != configuration.expected_serial_number:
            raise RuntimeError(
                "Connected serial number does not match expected_serial_number"
            )
        initial_status = instrument.read_status()
        report["initial_status"] = to_jsonable(initial_status)
        require_safe_start(initial_status)
        initial_watchdog = instrument.read_watchdog()
        report["initial_watchdog"] = initial_watchdog
        if initial_watchdog:
            raise RuntimeError("The supervised CC runner requires the watchdog to be disabled initially")
        cleanup_authorized = True

        instrument.configure_safety_limits(configuration.safety_limits)
        limits_configured = True
        limits_readback = instrument.read_safety_limits()
        mismatches = safety_limit_mismatches(
            configuration.safety_limits, limits_readback
        )
        report["safety_limits_requested"] = to_jsonable(
            configuration.safety_limits
        )
        report["safety_limits_readback"] = to_jsonable(limits_readback)
        report["safety_limits_not_programmed"] = ["uut_temperature_max"]
        report["safety_limit_mismatches"] = mismatches
        if mismatches:
            raise RuntimeError(
                "Safety-limit readback differs from the approved profile: "
                + "; ".join(mismatches)
            )

        if args.preflight_only:
            report["preflight_passed"] = True
        else:
            if configuration.cc_hold.watchdog_enabled:
                instrument.set_watchdog(True)
                if not instrument.read_watchdog():
                    raise RuntimeError("Watchdog enable was not read back")
            routine = configuration.cc_hold.routine(
                configuration.safety_limits,
                configure_limits=False,
            )
            routine_result = RoutineRunner(instrument, collector).run(routine)
            report["routine_result"] = to_jsonable(routine_result)
            report["acquisition"] = to_jsonable(collector.statistics())
            report["csv_path"] = (
                str(collector.csv_path.resolve()) if collector.csv_path else None
            )
            if routine_result.state != RoutineState.PASSED:
                raise RuntimeError(
                    f"Routine {routine_result.state.value}: {routine_result.reason}"
                )
            if collector.error:
                raise RuntimeError(f"Acquisition failed: {collector.error}")
            if collector.csv_path is None:
                raise RuntimeError("The supervised CC acquisition did not produce a CSV")
            current_response = _current_response(collector.csv_path, configuration)
            report["current_response"] = current_response
            if not current_response["passed"]:
                raise RuntimeError(
                    "Measured current did not meet the approved response criteria"
                )
    except KeyboardInterrupt:
        report["error"] = "KeyboardInterrupt: operator requested stop"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if routine_result is not None and "acquisition" not in report:
            report["acquisition"] = to_jsonable(collector.statistics())
            report["csv_path"] = (
                str(collector.csv_path.resolve()) if collector.csv_path else None
            )
        if cleanup_authorized:
            try:
                cleanup_failures: list[str] = []
                operations = [
                    (
                        "emergency_stop",
                        lambda: instrument.emergency_stop(
                            "Supervised CC final cleanup"
                        ),
                    )
                ]
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
                    [
                        ("disable", instrument.disable),
                        ("disable_watchdog", lambda: instrument.set_watchdog(False)),
                    ]
                )
                for name, operation in operations:
                    try:
                        operation()
                    except Exception as exc:
                        cleanup_failures.append(f"{name}: {type(exc).__name__}: {exc}")
                if cleanup_failures:
                    raise RuntimeError("; ".join(cleanup_failures))
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
        print(f"CC report: {report_path.resolve()}")
        print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
