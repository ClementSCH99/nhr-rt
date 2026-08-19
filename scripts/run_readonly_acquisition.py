"""Sustained, strictly read-only validation on a real NHR9300.

The object exposed to this script only contains read methods.  This is a
deliberate second boundary in addition to the non-resetting IVI initialization.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from nhr9300 import NHR9300, StaticInterlockProvider
from nhr9300.acquisition import AcquisitionCollector
from nhr9300.backends.ivi import IVIBackend
from nhr9300.types import InstrumentStatus, to_jsonable


class ReadOnlyInstrument(Protocol):
    """The complete instrument API authorized by this read-only runner."""

    instrument_id: str

    def read_identity(self): ...
    def read_capabilities(self): ...
    def read_status(self) -> InstrumentStatus: ...
    def read_measurement(self): ...
    def check_runtime_safety(self) -> None: ...


def state_signature(status: InstrumentStatus) -> dict[str, object]:
    """Fields that must not be changed by read-only acquisition."""
    return {
        "enabled": status.enabled,
        "state": status.state,
        "setpoints": status.setpoints,
    }


def wait_for_duration(collector: AcquisitionCollector, duration_s: float) -> None:
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        if collector.error:
            raise RuntimeError(f"Acquisition stopped: {collector.error}")
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def acquire_phase(
    instrument: ReadOnlyInstrument,
    *,
    rate_hz: float,
    duration_s: float,
    csv_path: Path,
) -> dict[str, object]:
    collector = AcquisitionCollector(
        instrument, rate_hz=rate_hz, csv_path=csv_path  # type: ignore[arg-type]
    )
    before = instrument.read_status()
    collector.start()
    try:
        wait_for_duration(collector, duration_s)
    finally:
        collector.stop()
    after = instrument.read_status()
    unchanged = state_signature(before) == state_signature(after)
    return {
        "rate_hz": rate_hz,
        "duration_s": duration_s,
        "csv_path": str(collector.csv_path),
        "statistics": asdict(collector.statistics()),
        "status_before": to_jsonable(before),
        "status_after": to_jsonable(after),
        "state_unchanged": unchanged,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate sustained NHR9300 reads without issuing writes."
    )
    parser.add_argument(
        "--resource",
        default=os.environ.get("NHR9300_RESOURCE"),
        help="NHR logical resource (or set NHR9300_RESOURCE), e.g. 'DC PM 1'",
    )
    parser.add_argument("--duration", type=float, default=60.0, help="Seconds per rate")
    parser.add_argument(
        "--rates", type=float, nargs="+", default=[1.0, 5.0, 10.0]
    )
    parser.add_argument("--reconnects", type=int, default=2)
    parser.add_argument("--output", type=Path, default=Path("readonly-results"))
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.resource:
        raise SystemExit("Specify --resource or set NHR9300_RESOURCE.")
    if args.duration <= 0:
        raise SystemExit("--duration must be positive.")
    if not args.rates or any(not 1.0 <= rate <= 10.0 for rate in args.rates):
        raise SystemExit("Every --rates value must be between 1 and 10 Hz.")
    if args.reconnects < 0:
        raise SystemExit("--reconnects cannot be negative.")


def main() -> int:
    args = parse_args()
    validate_args(args)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output / run_id
    output.mkdir(parents=True, exist_ok=False)
    instrument = NHR9300(
        "hardware",
        IVIBackend("hardware", args.resource),
        interlocks=[StaticInterlockProvider(safe=False)],
    )
    report: dict[str, object] = {
        "run_id": run_id,
        "resource": args.resource,
        "started_at_utc": datetime.now(timezone.utc),
        "strictly_read_only": True,
        "phases": [],
        "reconnections": [],
        "passed": False,
    }
    try:
        with instrument:
            report["identity"] = to_jsonable(instrument.read_identity())
            report["capabilities"] = to_jsonable(instrument.read_capabilities())
            initial = instrument.read_status()
            report["initial_status"] = to_jsonable(initial)
            for rate in args.rates:
                phase = acquire_phase(
                    instrument,
                    rate_hz=rate,
                    duration_s=args.duration,
                    csv_path=output / f"measurements-{rate:g}hz.csv",
                )
                report["phases"].append(phase)  # type: ignore[union-attr]
                if not phase["state_unchanged"]:
                    raise RuntimeError(f"State changed during {rate:g} Hz acquisition")

        for attempt in range(1, args.reconnects + 1):
            instrument.connect()
            status = instrument.read_status()
            instrument.read_measurement()
            instrument.close()
            unchanged = state_signature(status) == state_signature(initial)
            report["reconnections"].append(  # type: ignore[union-attr]
                {
                    "attempt": attempt,
                    "status": to_jsonable(status),
                    "state_unchanged": unchanged,
                    "passed": unchanged,
                }
            )
            if not unchanged:
                raise RuntimeError(
                    f"State changed after reconnection attempt {attempt}"
                )
        report["passed"] = True
        return 0
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
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
        print(f"Read-only report: {report_path.resolve()}")
        print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")


if __name__ == "__main__":
    raise SystemExit(main())
