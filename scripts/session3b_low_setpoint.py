"""Session 3B: one short, supervised low-setpoint transition."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nhr9300 import NHR9300, OperatingState, StaticInterlockProvider
from nhr9300.acquisition import AcquisitionCollector
from nhr9300.backends.ivi import IVIBackend
from nhr9300.safety_validation import (
    LowSetpointValidator,
    PhaseBProfile,
    require_safe_start,
)
from nhr9300.types import to_jsonable

try:
    from session3_safety import load_limits
except ModuleNotFoundError:
    from scripts.session3_safety import load_limits


WRITE_ACK = "SUPERVISED_SESSION3B_LOW_SETPOINT_READY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one supervised Session 3B low-setpoint transition."
    )
    parser.add_argument(
        "--resource",
        default=os.environ.get("NHR9300_RESOURCE"),
        help="NHR logical resource, e.g. 'DC PM 1'",
    )
    parser.add_argument("--bench-profile", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("session3b-results"))
    return parser.parse_args()


def load_phase_b(data: dict[str, Any]) -> PhaseBProfile:
    raw = data.get("phase_b")
    if not isinstance(raw, dict):
        raise ValueError("The profile must contain a phase_b object")
    values = dict(raw)
    values["mode"] = OperatingState[str(values["mode"]).upper()]
    return PhaseBProfile(**values)


def main() -> int:
    args = parse_args()
    if not args.resource:
        raise SystemExit("Specify --resource or set NHR9300_RESOURCE.")
    if os.environ.get("NHR9300_SESSION3B_ACK") != WRITE_ACK:
        raise SystemExit(
            f"Set NHR9300_SESSION3B_ACK={WRITE_ACK} after the 3B bench review."
        )

    profile_data, limits = load_limits(args.bench_profile)
    phase_b = load_phase_b(profile_data)
    LowSetpointValidator.validate_profile(phase_b, limits)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output / run_id
    output.mkdir(parents=True, exist_ok=False)
    instrument = NHR9300(
        "hardware",
        IVIBackend("hardware", args.resource),
        interlocks=[StaticInterlockProvider(safe=True, name="operator_supervised")],
    )
    collector = AcquisitionCollector(
        instrument,
        rate_hz=10.0,
        csv_path=output / "measurements.csv",
    )
    report: dict[str, Any] = {
        "session": "3B",
        "run_id": run_id,
        "resource": args.resource,
        "profile_file": str(args.bench_profile.resolve()),
        "phase_b_profile": to_jsonable(phase_b),
        "direct_enable_called": False,
        "watchdog_changed": False,
        "started_at_utc": datetime.now(timezone.utc),
        "passed": False,
    }
    cleanup_authorized = False
    try:
        instrument.connect()
        initial_status = instrument.read_status()
        report["initial_status"] = to_jsonable(initial_status)
        require_safe_start(initial_status)
        cleanup_authorized = True
        collector.start()
        result = LowSetpointValidator(instrument).run(phase_b, limits)
        report["result"] = to_jsonable(result)
        report["passed"] = True
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        try:
            collector.stop()
            report["acquisition"] = to_jsonable(collector.statistics())
            report["csv_path"] = (
                str(collector.csv_path) if collector.csv_path else None
            )
        except Exception as exc:
            report["collector_error"] = f"{type(exc).__name__}: {exc}"
            report["passed"] = False
        if cleanup_authorized:
            try:
                instrument.emergency_stop("Session 3B final cleanup")
                report["cleanup_status"] = to_jsonable(instrument.read_status())
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
        report_path = output / "report.json"
        report_path.write_text(
            json.dumps(to_jsonable(report), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Session 3B report: {report_path.resolve()}")
        print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")


if __name__ == "__main__":
    raise SystemExit(main())
