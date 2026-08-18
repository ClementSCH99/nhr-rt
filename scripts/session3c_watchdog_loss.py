"""Session 3C: supervised watchdog response to an abrupt process loss."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from nhr9300 import NHR9300, OperatingState, Setpoints, StaticInterlockProvider
from nhr9300.backends.ivi import IVIBackend
from nhr9300.safety_validation import (
    PhaseCProfile,
    WatchdogLossValidator,
    require_disabled_inactive,
    require_safe_start,
)
from nhr9300.types import SafetyLimits, to_jsonable

try:
    from session3_safety import load_limits
except ModuleNotFoundError:
    from scripts.session3_safety import load_limits


WRITE_ACK = "SUPERVISED_SESSION3C_WATCHDOG_LOSS_READY"
WORKER_TIMEOUT_S = 15.0


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
    parser.add_argument(
        "--connection-loss-preflight",
        action="store_true",
        help="Test abrupt process loss while the NHR remains disabled.",
    )
    parser.add_argument("--worker-mode", help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def load_phase_c(data: dict[str, Any]) -> PhaseCProfile:
    raw = data.get("phase_c")
    if not isinstance(raw, dict):
        raise ValueError("The profile must contain a phase_c object")
    values = dict(raw)
    values["mode"] = OperatingState[str(values["mode"]).upper()]
    return PhaseCProfile(**values)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Flush evidence before a deliberate process exit or possible IVI wait."""
    with path.open("w", encoding="utf-8") as stream:
        json.dump(to_jsonable(payload), stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())


def require_acknowledgement(args: argparse.Namespace) -> None:
    if not args.resource:
        raise SystemExit("Specify --resource or set NHR9300_RESOURCE.")
    if os.environ.get("NHR9300_SESSION3C_ACK") != WRITE_ACK:
        raise SystemExit(
            f"Set NHR9300_SESSION3C_ACK={WRITE_ACK} after the 3C bench review."
        )


def make_instrument(resource: str) -> NHR9300:
    return NHR9300(
        "hardware",
        IVIBackend("hardware", resource),
        interlocks=[StaticInterlockProvider(safe=True, name="operator_supervised")],
    )


def prepare_worker(
    args: argparse.Namespace,
    phase_c: PhaseCProfile,
    limits: SafetyLimits,
    *,
    active: bool,
) -> int:
    """Persist a safe/active snapshot, then exit without closing IVI-COM."""
    assert args.worker_output is not None
    evidence: dict[str, Any] = {
        "stage": "starting",
        "active_test": active,
        "ready_for_abrupt_exit": False,
        "started_at_utc": datetime.now(timezone.utc),
    }
    write_json(args.worker_output, evidence)
    instrument = make_instrument(args.resource)
    cleanup_authorized = False
    initial_watchdog = True
    try:
        evidence["stage"] = "connecting"
        write_json(args.worker_output, evidence)
        instrument.connect()
        evidence["stage"] = "connected"
        initial_status = instrument.read_status()
        evidence["initial_status"] = to_jsonable(initial_status)
        require_safe_start(initial_status)
        cleanup_authorized = True
        initial_watchdog = instrument.read_watchdog()
        evidence["initial_watchdog"] = initial_watchdog

        if active:
            active_result = WatchdogLossValidator(instrument).prepare_active(
                phase_c, limits
            )
            evidence["active_evidence"] = to_jsonable(active_result)

        evidence["stage"] = "ready_for_abrupt_exit"
        evidence["loss_started_at_utc"] = datetime.now(timezone.utc)
        evidence["ready_for_abrupt_exit"] = True
        write_json(args.worker_output, evidence)

        # Intentional fault: skip IVI Close, COM teardown and Python finalizers.
        os._exit(0)
    except Exception as exc:
        evidence["stage"] = "failed"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        if cleanup_authorized and active:
            try:
                WatchdogLossValidator(instrument).restore_safe_state(
                    disable_watchdog=not initial_watchdog
                )
                evidence["cleanup_status"] = to_jsonable(
                    instrument.read_status()
                )
                evidence["cleanup_watchdog"] = instrument.read_watchdog()
            except Exception as cleanup_exc:
                evidence["cleanup_error"] = (
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                )
        try:
            instrument.close()
        except Exception as close_exc:
            evidence["close_error"] = f"{type(close_exc).__name__}: {close_exc}"
        evidence["ended_at_utc"] = datetime.now(timezone.utc)
        write_json(args.worker_output, evidence)
        return 1


def recovery_worker(
    args: argparse.Namespace,
    limits: SafetyLimits,
    *,
    active: bool,
) -> int:
    """Reconnect, save the observed state first, then clean an active test."""
    assert args.worker_output is not None
    evidence: dict[str, Any] = {
        "stage": "connecting",
        "active_test": active,
        "passed": False,
        "started_at_utc": datetime.now(timezone.utc),
    }
    write_json(args.worker_output, evidence)
    instrument = make_instrument(args.resource)
    connected = False
    criterion_error: Exception | None = None
    try:
        instrument.connect()
        connected = True
        status = instrument.read_status()
        watchdog = instrument.read_watchdog()
        evidence["stage"] = "observed_after_reconnect"
        evidence["status_after_reconnect"] = to_jsonable(status)
        evidence["watchdog_after_reconnect"] = watchdog
        write_json(args.worker_output, evidence)
        try:
            require_disabled_inactive(status)
        except Exception as exc:
            criterion_error = exc
            evidence["criterion_error"] = f"{type(exc).__name__}: {exc}"

        if active:
            instrument.emergency_stop("Session 3C recovery worker cleanup")
            instrument.configure_safety_limits(limits)
            instrument.configure_setpoints(Setpoints(state=OperatingState.STANDBY))
            instrument.disable()
            instrument.set_watchdog(False)
            evidence["cleanup_status"] = to_jsonable(instrument.read_status())
            evidence["cleanup_watchdog"] = instrument.read_watchdog()

        if criterion_error is not None:
            raise criterion_error
        evidence["passed"] = True
        evidence["stage"] = "completed"
        return 0
    except Exception as exc:
        evidence["stage"] = "failed"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        return 1
    finally:
        if connected:
            try:
                instrument.close()
            except Exception as close_exc:
                evidence["close_error"] = f"{type(close_exc).__name__}: {close_exc}"
                evidence["passed"] = False
        evidence["ended_at_utc"] = datetime.now(timezone.utc)
        write_json(args.worker_output, evidence)


def worker_main(args: argparse.Namespace) -> int:
    require_acknowledgement(args)
    if args.worker_output is None:
        raise SystemExit("Internal worker output path is required.")
    profile_data, limits = load_limits(args.bench_profile)
    phase_c = load_phase_c(profile_data)
    WatchdogLossValidator.validate_profile(phase_c, limits)
    if args.worker_mode == "prepare-safe":
        return prepare_worker(args, phase_c, limits, active=False)
    if args.worker_mode == "prepare-active":
        return prepare_worker(args, phase_c, limits, active=True)
    if args.worker_mode == "recover-safe":
        return recovery_worker(args, limits, active=False)
    if args.worker_mode == "recover-active":
        return recovery_worker(args, limits, active=True)
    raise SystemExit("Unknown internal worker mode.")


def worker_environment() -> dict[str, str]:
    """Run the real 32-bit interpreter directly, while retaining venv packages."""
    environment = os.environ.copy()
    roots = [
        str(Path(__file__).resolve().parents[1] / "src"),
        str(Path(sys.prefix) / "Lib" / "site-packages"),
    ]
    existing = environment.get("PYTHONPATH")
    if existing:
        roots.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(roots)
    return environment


def run_worker(
    args: argparse.Namespace,
    mode: str,
    output: Path,
) -> tuple[int, bool]:
    executable = Path(getattr(sys, "_base_executable", sys.executable))
    command = [
        str(executable),
        str(Path(__file__).resolve()),
        "--resource",
        args.resource,
        "--bench-profile",
        str(args.bench_profile.resolve()),
        "--worker-mode",
        mode,
        "--worker-output",
        str(output.resolve()),
    ]
    process = subprocess.Popen(
        command,
        env=worker_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        return process.wait(timeout=WORKER_TIMEOUT_S), False
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)
        return -1, True


def read_evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"stage": "missing", "error": "Worker evidence file is missing"}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    if args.worker_mode is not None:
        return worker_main(args)
    require_acknowledgement(args)

    profile_data, limits = load_limits(args.bench_profile)
    phase_c = load_phase_c(profile_data)
    WatchdogLossValidator.validate_profile(phase_c, limits)
    active = not args.connection_loss_preflight
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output / run_id
    output.mkdir(parents=True, exist_ok=False)
    prepare_path = output / "prepare-evidence.json"
    recovery_path = output / "recovery-evidence.json"
    report: dict[str, Any] = {
        "session": "3C" if active else "3C connection-loss preflight",
        "run_id": run_id,
        "resource": args.resource,
        "profile_file": str(args.bench_profile.resolve()),
        "safety_limits_requested": to_jsonable(limits),
        "phase_c_profile": to_jsonable(phase_c),
        "loss_method": "abrupt worker process exit without IVI Close",
        "active_test": active,
        "started_at_utc": datetime.now(timezone.utc),
        "passed": False,
    }

    prepare_mode = "prepare-active" if active else "prepare-safe"
    prepare_code, prepare_timeout = run_worker(args, prepare_mode, prepare_path)
    prepare_evidence = read_evidence(prepare_path)
    report["prepare_exit_code"] = prepare_code
    report["prepare_timeout"] = prepare_timeout
    report["prepare_evidence"] = prepare_evidence
    ready = prepare_code == 0 and prepare_evidence.get("ready_for_abrupt_exit")

    # Only characterize the requested gap after the worker proved it reached loss.
    if ready:
        time.sleep(phase_c.disconnect_duration_s if active else 1.0)

    recovery_mode = "recover-active" if active else "recover-safe"
    recovery_code, recovery_timeout = run_worker(
        args, recovery_mode, recovery_path
    )
    recovery_evidence = read_evidence(recovery_path)
    report["recovery_exit_code"] = recovery_code
    report["recovery_timeout"] = recovery_timeout
    report["recovery_evidence"] = recovery_evidence

    report["passed"] = bool(
        ready
        and not prepare_timeout
        and recovery_code == 0
        and not recovery_timeout
        and recovery_evidence.get("passed")
    )
    if not report["passed"]:
        report["error"] = "Preparation or recovery did not complete safely"
    report["ended_at_utc"] = datetime.now(timezone.utc)
    report_path = output / "report.json"
    write_json(report_path, report)
    print(f"Session 3C report: {report_path.resolve()}")
    print(f"Result: {'PASS' if report['passed'] else 'FAIL'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
