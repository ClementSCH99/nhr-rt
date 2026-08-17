"""Session 3A: supervised validation of isolated, non-energizing writes."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nhr9300 import NHR9300, SafetyLimits, StaticInterlockProvider
from nhr9300.backends.ivi import IVIBackend
from nhr9300.safety_validation import (
    SafetyPrimitiveValidator,
    require_disabled_inactive,
    require_safe_start,
    safety_limit_mismatches,
)
from nhr9300.types import to_jsonable


WRITE_ACK = "SUPERVISED_SESSION3_WRITES_READY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate disable, limits and standby on a supervised NHR9300. "
            "This phase never calls enable()."
        )
    )
    parser.add_argument(
        "--resource",
        default=os.environ.get("NHR9300_RESOURCE"),
        help="NHR logical resource, e.g. 'DC PM 1'",
    )
    parser.add_argument(
        "--bench-profile",
        required=True,
        type=Path,
        help="Reviewed JSON containing approved safety_limits",
    )
    parser.add_argument("--output", type=Path, default=Path("session3-results"))
    return parser.parse_args()


def load_limits(path: Path) -> tuple[dict[str, Any], SafetyLimits]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("safety_limits"), dict):
        raise ValueError("The profile must contain a safety_limits object")
    limits = SafetyLimits(**data["safety_limits"])
    if not limits.approved or not limits.profile_name.strip():
        raise ValueError("The safety_limits must be explicitly approved and named")
    return data, limits


def main() -> int:
    args = parse_args()
    if not args.resource:
        raise SystemExit("Specify --resource or set NHR9300_RESOURCE.")
    if os.environ.get("NHR9300_SESSION3_ACK") != WRITE_ACK:
        raise SystemExit(
            f"Set NHR9300_SESSION3_ACK={WRITE_ACK} after the bench review."
        )

    profile, limits = load_limits(args.bench_profile)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output / run_id
    output.mkdir(parents=True, exist_ok=False)
    instrument = NHR9300(
        "hardware",
        IVIBackend("hardware", args.resource),
        interlocks=[
            StaticInterlockProvider(safe=True, name="operator_supervised")
        ],
    )
    report: dict[str, Any] = {
        "session": "3A",
        "run_id": run_id,
        "resource": args.resource,
        "profile_file": str(args.bench_profile.resolve()),
        "bench_description": profile.get("bench_description", ""),
        "stop_procedure": profile.get("stop_procedure", ""),
        "started_at_utc": datetime.now(timezone.utc),
        "enable_called": False,
        "watchdog_changed": False,
        "primitives": [],
        "passed": False,
    }
    cleanup_authorized = False

    try:
        instrument.connect()
        report["identity"] = to_jsonable(instrument.read_identity())
        initial_status = instrument.read_status()
        report["initial_status"] = to_jsonable(initial_status)
        require_safe_start(initial_status)
        cleanup_authorized = True
        results = SafetyPrimitiveValidator(instrument).run_phase_a(limits)
        report["primitives"] = to_jsonable(results)
        readback = instrument.read_safety_limits()
        mismatches = safety_limit_mismatches(limits, readback)
        report["safety_limits_requested"] = to_jsonable(limits)
        report["safety_limits_readback"] = to_jsonable(readback)
        report["safety_limits_not_programmed"] = (
            ["uut_temperature_max"]
            if limits.uut_temperature_max is None
            else []
        )
        report["safety_limits_verified"] = not mismatches
        report["safety_limit_mismatches"] = mismatches
        if mismatches:
            raise RuntimeError(
                "Safety-limit readback differs from the approved profile: "
                + "; ".join(mismatches)
            )

        instrument.close()
        instrument.connect()
        reconnected = instrument.read_status()
        require_disabled_inactive(reconnected)
        report["status_after_reconnect"] = to_jsonable(reconnected)
        report["passed"] = True
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        if cleanup_authorized:
            try:
                instrument.disable()
                report["cleanup_status"] = to_jsonable(instrument.read_status())
            except Exception as exc:
                report["safe_cleanup_error"] = f"{type(exc).__name__}: {exc}"
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
        print(f"Session 3A report: {report_path.resolve()}")
        print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")


if __name__ == "__main__":
    raise SystemExit(main())
