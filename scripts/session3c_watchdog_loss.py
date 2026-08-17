"""Session 3C: supervised watchdog response to a communication gap."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from nhr9300 import NHR9300, OperatingState, Setpoints, StaticInterlockProvider
from nhr9300.backends.ivi import IVIBackend
from nhr9300.safety_validation import (
    PhaseCProfile,
    WatchdogLossValidator,
    require_safe_start,
)
from nhr9300.types import to_jsonable

try:
    from session3_safety import load_limits
except ModuleNotFoundError:
    from scripts.session3_safety import load_limits


WRITE_ACK = "SUPERVISED_SESSION3C_WATCHDOG_LOSS_READY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one supervised Session 3C watchdog-loss test."
    )
    parser.add_argument(
        "--resource",
        default=os.environ.get("NHR9300_RESOURCE"),
        help="NHR logical resource, e.g. 'DC PM 1'",
    )
    parser.add_argument("--bench-profile", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("session3c-results"))
    return parser.parse_args()


def load_phase_c(data: dict[str, Any]) -> PhaseCProfile:
    raw = data.get("phase_c")
    if not isinstance(raw, dict):
        raise ValueError("The profile must contain a phase_c object")
    values = dict(raw)
    values["mode"] = OperatingState[str(values["mode"]).upper()]
    return PhaseCProfile(**values)


def main() -> int:
    args = parse_args()
    if not args.resource:
        raise SystemExit("Specify --resource or set NHR9300_RESOURCE.")
    if os.environ.get("NHR9300_SESSION3C_ACK") != WRITE_ACK:
        raise SystemExit(
            f"Set NHR9300_SESSION3C_ACK={WRITE_ACK} after the 3C bench review."
        )

    profile_data, limits = load_limits(args.bench_profile)
    phase_c = load_phase_c(profile_data)
    WatchdogLossValidator.validate_profile(phase_c, limits)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output / run_id
    output.mkdir(parents=True, exist_ok=False)
    instrument = NHR9300(
        "hardware",
        IVIBackend("hardware", args.resource),
        interlocks=[StaticInterlockProvider(safe=True, name="operator_supervised")],
    )
    report: dict[str, Any] = {
        "session": "3C",
        "run_id": run_id,
        "resource": args.resource,
        "profile_file": str(args.bench_profile.resolve()),
        "safety_limits_requested": to_jsonable(limits),
        "phase_c_profile": to_jsonable(phase_c),
        "direct_enable_called": False,
        "started_at_utc": datetime.now(timezone.utc),
        "passed": False,
    }
    cleanup_authorized = False
    watchdog_cleanup_authorized = False
    try:
        instrument.connect()
        initial_status = instrument.read_status()
        report["initial_status"] = to_jsonable(initial_status)
        require_safe_start(initial_status)
        cleanup_authorized = True
        initial_watchdog = instrument.read_watchdog()
        report["initial_watchdog"] = initial_watchdog
        watchdog_cleanup_authorized = not initial_watchdog
        result = WatchdogLossValidator(instrument).run(phase_c, limits)
        report["result"] = to_jsonable(result)
        report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if cleanup_authorized:
            try:
                instrument.connect()
                instrument.configure_setpoints(
                    Setpoints(state=OperatingState.STANDBY)
                )
                instrument.disable()
                if watchdog_cleanup_authorized:
                    instrument.set_watchdog(False)
                report["cleanup_status"] = to_jsonable(instrument.read_status())
                report["cleanup_watchdog"] = instrument.read_watchdog()
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
        print(f"Session 3C report: {report_path.resolve()}")
        print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
