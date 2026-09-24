"""Guided local operator console; all instrument actions use the public API.

Preparation edits only a reviewed registry digest. Starting a service is an
explicit action because service startup connects configured instruments.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from .client import NHRServiceClient
from .diagnostics import diagnose_config
from .evidence import atomic_write_json
from .errors import NHRAPIError
from .monitor import require_local_service_url
from .workflow_registry import WorkflowBundle

ACK = "SUPERVISED_WORKFLOW_READY"
SHUTDOWN_ACK = "CONTROLLED_SERVICE_SHUTDOWN"


def _python_runtime(executable: Path) -> tuple[int, bool, bool]:
    """Inspect a candidate service interpreter without importing IVI-COM."""
    probe = (
        "import importlib.util, struct; "
        "print(struct.calcsize('P') * 8, "
        "int(importlib.util.find_spec('comtypes') is not None), "
        "int(importlib.util.find_spec('nhr9300') is not None))"
    )
    try:
        result = subprocess.run(
            [str(executable), "-c", probe],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
        bitness, comtypes_available, package_available = result.stdout.strip().split()
        return int(bitness), comtypes_available == "1", package_available == "1"
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0, False, False


def _service_backend(config: Path, instrument_id: str) -> str:
    value = json.loads(config.read_text(encoding="utf-8"))
    try:
        instrument = next(
            item for item in value.get("instruments", []) if item.get("id") == instrument_id
        )
    except StopIteration as exc:
        raise ValueError(f"Instrument {instrument_id!r} is absent from the configuration") from exc
    return str(instrument.get("backend", "ivi"))


def configured_instrument_ids(config: Path) -> tuple[str, ...]:
    """Return configured IDs and reject malformed or empty instrument lists."""
    value = json.loads(config.read_text(encoding="utf-8"))
    raw = value.get("instruments")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Configuration must define at least one instrument")
    identifiers = tuple(
        str(item.get("id", "")).strip()
        for item in raw
        if isinstance(item, dict)
    )
    if len(identifiers) != len(raw) or any(not item for item in identifiers):
        raise ValueError("Every configured instrument requires a non-empty id")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Configured instrument IDs must be unique")
    return identifiers


def resolve_service_python(config: Path, instrument_id: str, requested: str | None) -> str:
    """Choose a valid service interpreter before a physical connection is attempted."""
    backend = _service_backend(config, instrument_id)
    if backend != "ivi":
        return requested or sys.executable

    if requested:
        candidates = [Path(requested)]
    else:
        current = Path(sys.executable)
        candidates = [current]
        if current.parent.name.lower() == "scripts":
            candidates.append(current.parent.parent.parent / ".venv32" / "Scripts" / "python.exe")
        for parent in (config.parent, *config.parents):
            candidates.append(parent / ".venv32" / "Scripts" / "python.exe")

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        if _python_runtime(candidate) == (32, True, True):
            return str(candidate)

    detail = f"Configured --service-python {requested!r} is not" if requested else "No interpreter is"
    raise ValueError(
        f"{detail} a usable 32-bit NHR service runtime with comtypes and nhr9300. "
        "Install .[ivi] in .venv32 or pass --service-python .\\.venv32\\Scripts\\python.exe"
    )


def prepared_config(path: Path, workflow_id: str) -> tuple[dict, WorkflowBundle]:
    """Validate approvals/identity before calculating a replacement digest."""
    config = json.loads(path.read_text(encoding="utf-8"))
    entry = next(item for item in config["workflow_registry"]
                 if item["workflow_id"] == workflow_id)
    if entry.get("approved") is not True:
        raise ValueError("Registry entry is not approved; review it before preparation")
    instrument = next(item for item in config["instruments"]
                      if item["id"] == entry["instrument_id"])
    profile = Path(entry["profile_path"])
    if not profile.is_absolute():
        profile = path.parent / profile
    bundle = WorkflowBundle.load(profile, hardware=instrument.get("backend", "ivi") == "ivi")
    for key in ("expected_resource", "expected_serial_number"):
        if entry.get(key) != getattr(bundle.configuration, key):
            raise ValueError(f"Registry/profile mismatch: {key}; review identity")
    entry["expected_bundle_digest"] = bundle.digest
    return config, bundle


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def launch_process(argv: list[str], log: Path) -> subprocess.Popen:
    """Launch without shell/console coupling; keep logs after this CLI exits."""
    log.parent.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with log.open("ab") as output:
        return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.STDOUT, **options)


def wait_ready(probe, process: subprocess.Popen, timeout_s: float = 15) -> None:
    """Bound startup waiting and leave a failed child/log identifiable."""
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Process {process.pid} exited; inspect its log")
        try:
            probe()
            return
        except Exception as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"Process {process.pid} not ready; retained for diagnosis: {last_error}")


class OperatorConsole:
    """One guided client; the service remains responsible for safety and runs."""

    def __init__(self, args, *, ask=input, show=print) -> None:
        self.args, self.ask, self.show = args, ask, show
        self.config = args.config.resolve()
        allowed = configured_instrument_ids(self.config)
        if args.instrument_id not in allowed:
            raise ValueError(
                f"Instrument {args.instrument_id!r} is absent from the configuration; "
                f"choose one of: {', '.join(allowed)}"
            )
        self.client = NHRServiceClient(args.service_url)
        self.selected: str | None = None
        self.preflight_digest: str | None = None
        self.log_dir = self.config.parent / "operator-logs"
        self.journal = self.log_dir / f"request-{args.instrument_id}.json"
        self.service_process: subprocess.Popen | None = None
        self.monitor_process: subprocess.Popen | None = None

    def display(self, value) -> None:
        self.show(json.dumps(value, indent=2, ensure_ascii=False, default=str))

    def verify_service(self) -> dict:
        config = self.client.configuration()
        if not config.get("config_file") or Path(config["config_file"]).resolve() != self.config:
            raise ValueError("Port belongs to a service with another config; select its config or another port")
        if not any(item["instrument_id"] == self.args.instrument_id for item in config["instruments"]):
            raise ValueError("Selected instrument is absent from this service")
        return config

    def launch(self) -> None:
        parsed = urlparse(self.args.service_url)
        host, port = parsed.hostname, parsed.port or 9300
        if port_open(host, port):
            self.verify_service()
            self.show(f"Joined existing service: {self.args.service_url}; not owned by this runner")
        else:
            if self.ask("Start service and connect configured instruments? Type CONNECT: ") != "CONNECT":
                return
            service_python = resolve_service_python(
                self.config, self.args.instrument_id, self.args.service_python
            )
            self.show(f"Service interpreter: {service_python}")
            log = self.log_dir / "service.log"
            process = launch_process([service_python, "-m", "nhr9300.service",
                                      "--config", str(self.config), "--host", host, "--port", str(port)], log)
            self.service_process = process
            self.show(f"Started service PID {process.pid}; log: {log}")
            wait_ready(self.verify_service, process)
        self.launch_monitor()
        self.status()

    def launch_monitor(self) -> None:
        url = f"http://127.0.0.1:{self.args.monitor_port}"

        def probe():
            with urlopen(url + "/api/config", timeout=1) as response:
                config = json.load(response)
            if (config.get("instrument_id") != self.args.instrument_id
                    or config.get("service_url") != self.args.service_url
                    or config.get("read_only") is not True):
                raise ValueError("Monitor port is occupied by another configuration; choose --monitor-port")

        if port_open("127.0.0.1", self.args.monitor_port):
            probe()
            self.show(f"Joined monitor: {url}")
        else:
            log = self.log_dir / "monitor.log"
            process = launch_process([sys.executable, "-m", "nhr9300.monitor", "--service-url",
                                      self.args.service_url, "--instrument-id", self.args.instrument_id,
                                      "--port", str(self.args.monitor_port)], log)
            self.monitor_process = process
            self.show(f"Started monitor PID {process.pid}; log: {log}")
            wait_ready(probe, process)
        webbrowser.open(url)

    def select(self) -> None:
        config = json.loads(self.config.read_text(encoding="utf-8"))
        entries = [item for item in config.get("workflow_registry", [])
                   if item["instrument_id"] == self.args.instrument_id]
        for item in entries:
            self.show(f"{item['workflow_id']} | approved={item.get('approved', False)} | {item['profile_path']}")
        chosen = self.ask("Workflow ID: ").strip()
        if chosen not in {item["workflow_id"] for item in entries}:
            raise ValueError("Unknown workflow ID")
        self.selected, self.preflight_digest = chosen, None

    def prepare(self) -> None:
        if not self.selected:
            raise ValueError("Select a workflow first")
        before = self.config.read_text(encoding="utf-8")
        updated, bundle = prepared_config(self.config, self.selected)
        self.show(f"Validated bundle: {bundle.digest}")
        for warning in bundle.configuration.warnings():
            self.show(f"WARNING: {warning}")
        after = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
        if json.loads(before) == updated:
            self.show("Configuration already matches; no update needed")
            return
        self.show("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                              fromfile=str(self.config), tofile="proposed configuration")))
        if self.ask("Apply this digest update? Type APPLY: ") == "APPLY":
            bundle.verify_unchanged()
            if self.config.read_text(encoding="utf-8") != before:
                raise ValueError("Configuration changed during review; prepare again")
            atomic_write_json(self.config, updated)
            self.preflight_digest = None
            self.show("Saved. Restart the service explicitly before preflight; no automatic restart.")

    def selected_digest(self) -> str:
        self.verify_service()
        if not self.selected:
            raise ValueError("Select a workflow first")
        updated, bundle = prepared_config(self.config, self.selected)
        on_disk = json.loads(self.config.read_text(encoding="utf-8"))
        if updated != on_disk:
            raise ValueError("Digest is outdated; prepare configuration, then restart the service")
        remote = next(item for item in self.client.workflows(self.args.instrument_id)
                      if item["workflow_id"] == self.selected)
        if not remote.get("available") or remote.get("bundle_digest") != bundle.digest:
            raise ValueError(f"Registered bundle unavailable or different; restart/check approvals: {remote.get('error')}")
        for warning in remote.get("warnings", []):
            self.show(f"WARNING: {warning}")
        return bundle.digest

    def preflight(self) -> None:
        self.preflight_digest = None
        digest = self.selected_digest()
        if self.ask("Run preflight (identity, limits readback and safe cleanup)? Type PREFLIGHT: ") != "PREFLIGHT":
            return
        result = self.client.preflight_workflow(self.args.instrument_id, self.selected, digest)
        self.display(result)
        if result.get("passed"):
            self.preflight_digest = digest

    def start(self) -> None:
        digest = self.selected_digest()
        if self.preflight_digest != digest:
            raise ValueError("Run a successful preflight for this selection first")
        if self.journal.exists():
            raise ValueError("A previous start request exists; use Recover request before starting another test")
        if self.ask(f"Start {self.selected}? Type {ACK}: ") != ACK:
            return
        request = {"request_id": str(uuid.uuid4()), "workflow_id": self.selected,
                   "bundle_digest": digest, "operator_acknowledgement": ACK}
        # Persist BEFORE sending: an uncertain HTTP response must never cause a
        # second run. Recovery retries the same service idempotency key.
        atomic_write_json(self.journal, request)
        result = self.client.start_workflow(self.args.instrument_id, **request)
        atomic_write_json(self.journal, {**request, "run_id": result["run_id"]})
        self.display(result)
        self.preflight_digest = None

    def recover(self) -> None:
        self.verify_service()
        request = json.loads(self.journal.read_text(encoding="utf-8"))
        self.display(request)
        run_id = request.pop("run_id", None)
        if run_id:
            result = self.client.workflow_run(self.args.instrument_id, run_id)
        else:
            if self.ask("Retry this exact start request (may start if never accepted)? Type RETRY: ") != "RETRY":
                return
            result = self.client.start_workflow(self.args.instrument_id, **request)
            atomic_write_json(self.journal, {**request, "run_id": result["run_id"]})
        self.display(result)
        if result["state"] in {"passed", "stopped", "failed", "interrupted"}:
            self.journal.unlink()
            self.show("Terminal request resolved; another test may be prepared")

    def status(self) -> dict:
        self.verify_service()
        snapshot = self.client.runtime(self.args.instrument_id)
        self.display(snapshot)
        self.show("Emergency stop / inhibit: state not determinable by current IVI status. "
                  "Check physical emergency stop/PowerPanel before preflight. Output DISABLED alone is normal.")
        return snapshot

    def stop(self) -> None:
        snapshot = self.status()
        run = snapshot["workflow"]
        if not run.get("active"):
            self.show("No active workflow. Last evidence:")
            self.display((run.get("last_run") or run).get("recording"))
            return
        result = self.client.stop_and_wait_workflow(self.args.instrument_id, run["run_id"], timeout_s=60)
        self.display(result)
        if self.journal.exists():
            pending = json.loads(self.journal.read_text(encoding="utf-8"))
            if pending.get("run_id") == result["run_id"]:
                self.journal.unlink()

    def end_stage(self) -> None:
        """Send the stage-end request before asking the operator for context."""
        self.verify_service()
        run = self.client.runtime(self.args.instrument_id)["workflow"]
        stage = run.get("stage")
        if not run.get("active") or stage is None:
            self.show("No active stage to end")
            return
        self.show(f"Ending stage {stage['index'] + 1}/{stage['count']}: {stage['name']}")
        intervention = self.client.end_workflow_stage(
            self.args.instrument_id, run["run_id"], stage["index"]
        )
        self.show("Stage-end request recorded; the service is completing the controlled transition")
        self.display(intervention)
        reason = self.ask("Reason (Enter keeps the recorded default): ").strip()
        if reason:
            try:
                updated = self.client.explain_workflow_stage_end(
                    self.args.instrument_id, run["run_id"],
                    intervention["intervention_id"], reason,
                )
            except NHRAPIError as exc:
                if exc.status != 409:
                    raise
                self.show("Evidence was already finalized; the recorded default reason remains")
            else:
                self.display(updated)

    def close_all(self) -> None:
        """Stop any run, then ask the service to release every owned resource."""
        self.verify_service()
        snapshot = self.client.runtime(self.args.instrument_id)
        run = snapshot["workflow"]
        if run.get("active"):
            if self.ask(
                "Stop the active workflow and wait for finalization? Type STOP: "
            ) != "STOP":
                self.show("Controlled shutdown cancelled; workflow and services remain active")
                return
            final = self.client.stop_and_wait_workflow(
                self.args.instrument_id,
                run["run_id"],
                timeout_s=60,
            )
            self.display(final)
            if self.journal.exists():
                pending = json.loads(self.journal.read_text(encoding="utf-8"))
                if pending.get("run_id") == final.get("run_id"):
                    self.journal.unlink()
            if not final.get("recording", {}).get("finalized"):
                self.show("WARNING: workflow evidence did not finalize successfully")
            if not final.get("final_safe_state", {}).get("verified"):
                self.show("WARNING: workflow final safe state was not verified")

        if self.ask(
            "Shut down the complete NHR service and release all configured instruments? "
            f"Type {SHUTDOWN_ACK}: "
        ) != SHUTDOWN_ACK:
            self.show("Controlled shutdown cancelled; services remain active")
            return

        result = self.client.shutdown_service(SHUTDOWN_ACK)
        self.display(result)
        if not (
            result.get("shutdown_completed")
            and result.get("safe_close_verified")
        ):
            raise RuntimeError(
                "Service did not confirm verified controlled shutdown"
            )
        parsed = urlparse(self.args.service_url)
        host, port = parsed.hostname, parsed.port or 9300
        deadline = time.monotonic() + 30.0
        while port_open(host, port) and time.monotonic() < deadline:
            time.sleep(0.1)
        if port_open(host, port):
            raise RuntimeError(
                "Service port remained open after controlled shutdown; inspect service.log"
            )

        if self.monitor_process is not None and self.monitor_process.poll() is None:
            self.monitor_process.terminate()
            self.monitor_process.wait(timeout=5)
            self.show("Runner-owned HMI process stopped")
        elif port_open("127.0.0.1", self.args.monitor_port):
            self.show(
                "The HMI was joined rather than started by this runner and remains open; "
                "it no longer owns or controls NHR resources"
            )
        self.show(
            "Controlled service shutdown completed; port closed and service-owned "
            "instrument resources released"
        )

    def run(self) -> int:
        actions = {"1": self.launch, "2": lambda: self.display(diagnose_config(self.config)),
                   "3": self.select, "4": self.prepare, "5": self.preflight,
                   "6": self.start, "7": self.status, "8": self.stop, "9": self.recover,
                   "10": self.close_all, "11": self.end_stage}
        while True:
            self.show(
                f"\nSelected: {self.selected or '-'}\n"
                "1 Launch/join NHR + HMI\n"
                "2 Diagnostic\n"
                "3 Select workflow\n"
                "4 Prepare digest\n"
                "5 Preflight\n"
                "6 Start\n"
                "7 Status/evidence\n"
                "8 Stop test and finalize\n"
                "9 Recover request\n"
                "10 Controlled shutdown NHR + runner-owned HMI\n"
                "11 End current stage early, then enter reason\n"
                "Q Leave services running"
            )
            try:
                choice = self.ask("> ").strip().lower()
                if choice == "q":
                    try:
                        self.status()
                    except Exception as exc:
                        self.show(f"State unknown: {exc}")
                    self.show("Runner closed. Services and any active workflow continue; no physical stop implied.")
                    return 0
                if choice in actions:
                    actions[choice]()
            except (EOFError, KeyboardInterrupt):
                self.show("Runner detached. Services/workflows may still be active; use the runner to inspect/stop.")
                return 0
            except Exception as exc:
                self.show(f"Blocked: {type(exc).__name__}: {exc}\nTransport failure does not establish safe state.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--instrument-id", required=True)
    parser.add_argument("--service-url", default="http://127.0.0.1:9300")
    parser.add_argument(
        "--service-python",
        help=("32-bit Python with comtypes and nhr9300 installed for IVI; "
              "auto-detect .venv32 when omitted, current interpreter for simulation"),
    )
    parser.add_argument("--monitor-port", type=int, default=9400)
    args = parser.parse_args()
    require_local_service_url(args.service_url)
    if not 1 <= args.monitor_port <= 65535:
        parser.error("monitor port must be in 1..65535")
    if not args.instrument_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.instrument_id):
        parser.error("instrument ID must contain letters, digits, underscore or hyphen")
    try:
        allowed = configured_instrument_ids(args.config.resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"invalid service configuration: {exc}")
    if args.instrument_id not in allowed:
        parser.error(
            f"instrument {args.instrument_id!r} is absent from the configuration; "
            f"choose one of: {', '.join(allowed)}"
        )
    return OperatorConsole(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
