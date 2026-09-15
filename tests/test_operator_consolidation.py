"""Software acceptance of recording barriers and guided operator gates."""
from __future__ import annotations

import hashlib
import json
import sys
import time
import uuid
import socket
import threading
from argparse import Namespace
from pathlib import Path

import pytest

from nhr9300.operator import OperatorConsole, prepared_config, resolve_service_python
from nhr9300.sinks import CsvMeasurementSink
from nhr9300.external_interlocks import ExternalInterlockManager
from test_external_interlocks import _rule, _now
from test_workflow_service import _start_server, _profile, _config


@pytest.fixture
def service(tmp_path):
    profile, config, server, manager, thread, client = _start_server(tmp_path, duration_s=0.6)
    path = tmp_path / "service.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    manager.config_path = path
    try:
        yield profile, config, manager, client, path
    finally:
        server.shutdown()
        thread.join(timeout=3)
        manager.close()
        server.server_close()


def start(client, config):
    return client.start_workflow("sim-remote", request_id=str(uuid.uuid4()),
                                workflow_id="approved-rest-v1",
                                bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"])


def test_finalized_files_stay_stable_across_observer_detach_and_second_run(service):
    _, config, manager, client, _ = service
    run = start(client, config)
    client.detach_observer("sim-remote")
    final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10, poll_interval_s=0.05)
    assert final["state"] == "passed"
    recording = final["recording"]
    assert recording["finalized"]
    manifest = json.loads(Path(recording["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["run_id"] == run["run_id"]
    assert "session_measurements" in {item["role"] for item in manifest["files"]}
    contents = {item["path"]: Path(item["path"]).read_bytes() for item in manifest["files"]}
    for item in manifest["files"]:
        assert hashlib.sha256(contents[item["path"]]).hexdigest() == item["sha256"]
    count = client.acquisition("sim-remote")["sample_count"]
    time.sleep(0.3)
    assert client.acquisition("sim-remote")["sample_count"] > count, client.acquisition("sim-remote")["last_error"]
    second = start(client, config)
    stopped = client.stop_and_wait_workflow("sim-remote", second["run_id"], timeout_s=10)
    assert stopped["state"] == "stopped"
    assert stopped["recording"]["finalized"]
    assert stopped["recording"]["path"] != recording["path"]
    again = client.stop_and_wait_workflow("sim-remote", second["run_id"], timeout_s=10)
    assert again["recording"] == stopped["recording"]
    assert all(Path(path).read_bytes() == content for path, content in contents.items())
    assert manager.get("sim-remote").collector.running


def test_failed_close_never_advertises_finalized_evidence(service, monkeypatch):
    _, config, _, client, _ = service
    original = CsvMeasurementSink.close
    failed_once = False

    def fail_session(self):
        nonlocal failed_once
        original(self)
        if self.path.name == "session.csv" and not failed_once:
            failed_once = True
            raise PermissionError("injected close failure")

    monkeypatch.setattr(CsvMeasurementSink, "close", fail_session)
    run = start(client, config)
    final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10)
    assert final["state"] == "failed"
    assert final["final_safe_state"]["verified"]
    assert not final["recording"]["finalized"]
    assert "injected close failure" in final["recording"]["error"]
    assert "files" not in final["recording"]
    assert not Path(final["recording"]["path"]).with_name("session-evidence.json").exists()
    # The failed evidence sink must not reserve the collector indefinitely.
    second = start(client, config)
    recovered = client.wait_workflow("sim-remote", second["run_id"], timeout_s=10)
    assert recovered["state"] == "passed"
    assert recovered["recording"]["finalized"]


def test_runtime_last_run_uses_persisted_chronology(service):
    _, _, manager, _, _ = service
    controller = manager.get("sim-remote").workflow_controller
    assert controller is not None
    newer = {
        "run_id": "newer",
        "instrument_id": "sim-remote",
        "state": "passed",
        "accepted_at_utc": "2026-09-14T14:00:00+00:00",
    }
    older = {
        "run_id": "older",
        "instrument_id": "sim-remote",
        "state": "passed",
        "accepted_at_utc": "2026-09-14T13:00:00+00:00",
    }
    # Deliberately insert the older run last to model arbitrary glob ordering.
    controller._runs = {"newer": newer, "older": older}
    assert controller.runtime_snapshot()["last_run"]["run_id"] == "newer"


def console(service, answers=()):
    _, _, _, client, path = service
    replies = iter(answers)
    args = Namespace(config=path, instrument_id="sim-remote", service_url=client.base_url.removesuffix("/api/v1"),
                     service_python=sys.executable, monitor_port=19400)
    operator = OperatorConsole(args, ask=lambda _: next(replies), show=lambda _: None)
    operator.selected = "approved-rest-v1"
    return operator


def test_guided_preflight_start_recover_and_second_run(service):
    operator = console(service, ["PREFLIGHT", "SUPERVISED_WORKFLOW_READY"])
    with pytest.raises(ValueError, match="preflight"):
        operator.start()
    operator.preflight()
    operator.start()
    journal = json.loads(operator.journal.read_text(encoding="utf-8"))
    operator.client.wait_workflow("sim-remote", journal["run_id"], timeout_s=10)
    # Known IDs recover by GET without prompting another launch.
    operator.recover()
    assert not operator.journal.exists()
    operator = console(service, ["PREFLIGHT", "SUPERVISED_WORKFLOW_READY"])
    operator.preflight()
    operator.start()
    operator.stop()
    assert not operator.journal.exists()


def test_lost_start_response_preserves_request_key(service, monkeypatch):
    operator = console(service, ["PREFLIGHT", "SUPERVISED_WORKFLOW_READY", "RETRY"])
    operator.preflight()
    actual = operator.client.start_workflow
    requests = []

    def lost(instrument, **request):
        requests.append(request["request_id"])
        actual(instrument, **request)
        raise ConnectionError("response lost")

    monkeypatch.setattr(operator.client, "start_workflow", lost)
    with pytest.raises(ConnectionError):
        operator.start()
    key = json.loads(operator.journal.read_text(encoding="utf-8"))["request_id"]
    monkeypatch.setattr(operator.client, "start_workflow", actual)
    operator.recover()
    recovered = json.loads(operator.journal.read_text(encoding="utf-8"))
    assert recovered["request_id"] == key == requests[0]
    operator.client.stop_and_wait_workflow("sim-remote", recovered["run_id"], timeout_s=10)


def test_preflight_failure_and_declined_ack_do_not_start(service, monkeypatch):
    operator = console(service, ["PREFLIGHT", "no"])
    monkeypatch.setattr(operator.client, "preflight_workflow", lambda *a: {"passed": False})
    operator.preflight()
    assert operator.preflight_digest is None
    with pytest.raises(ValueError, match="preflight"):
        operator.start()
    operator.preflight_digest = operator.selected_digest()
    operator.start()
    assert not operator.journal.exists()


def test_prepare_rejects_approval_and_drift_without_changing_config(service):
    profile, _, _, _, path = service
    before = path.read_bytes()
    value = json.loads(profile.read_text(encoding="utf-8"))
    value["safety_limits"]["approved"] = False
    profile.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="approved"):
        prepared_config(path, "approved-rest-v1")
    assert path.read_bytes() == before


def test_digest_preview_requires_apply_and_restart(service):
    profile, _, _, _, path = service
    profile.write_bytes(profile.read_bytes() + b"\n")
    operator = console(service, ["no", "APPLY"])
    before = path.read_bytes()
    operator.prepare()
    assert path.read_bytes() == before
    operator.prepare()
    assert path.read_bytes() != before
    with pytest.raises(ValueError, match="restart"):
        operator.selected_digest()


def test_launch_joins_only_matching_service(service, monkeypatch):
    operator = console(service)
    calls = []
    monkeypatch.setattr(operator, "launch_monitor", lambda: calls.append("monitor"))
    operator.launch()
    assert calls == ["monitor"]
    service[2].config_path = Path("other.json")
    with pytest.raises(ValueError, match="another config"):
        operator.launch()
    assert calls == ["monitor"]


def test_hardware_service_python_auto_detects_sibling_venv32(tmp_path, monkeypatch):
    import nhr9300.operator as module

    config = tmp_path / "approved" / "service.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"instruments": [{"id": "nhr", "backend": "ivi"}]}))
    python64 = tmp_path / ".venv64" / "Scripts" / "python.exe"
    python32 = tmp_path / ".venv32" / "Scripts" / "python.exe"
    python64.parent.mkdir(parents=True)
    python32.parent.mkdir(parents=True)
    python64.touch()
    python32.touch()
    monkeypatch.setattr(module.sys, "executable", str(python64))
    monkeypatch.setattr(
        module,
        "_python_runtime",
        lambda candidate: (32, True, True) if candidate == python32 else (64, False, True),
    )

    assert resolve_service_python(config, "nhr", None) == str(python32)


def test_hardware_service_python_rejects_wrong_runtime(tmp_path, monkeypatch):
    import nhr9300.operator as module

    config = tmp_path / "service.json"
    config.write_text(json.dumps({"instruments": [{"id": "nhr", "backend": "ivi"}]}))
    python64 = tmp_path / "python.exe"
    python64.touch()
    monkeypatch.setattr(module, "_python_runtime", lambda _: (64, False, True))

    with pytest.raises(ValueError, match="32-bit NHR service runtime with comtypes"):
        resolve_service_python(config, "nhr", str(python64))


def test_operator_menu_displays_one_action_per_line(service):
    operator = console(service)
    shown = []
    operator.show = shown.append
    operator.ask = lambda _: "q"
    operator.status = lambda: {}

    assert operator.run() == 0
    menu = shown[0].splitlines()
    assert menu[2:] == [
        "1 Launch/join NHR + HMI",
        "2 Diagnostic",
        "3 Select workflow",
        "4 Prepare digest",
        "5 Preflight",
        "6 Start",
        "7 Status/evidence",
        "8 Stop test and finalize",
        "9 Recover request",
        "Q Leave services running",
    ]


def test_template_library_remains_unapproved():
    for path in Path("examples/workflows").glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value["safety_limits"]["approved"] is False
        assert value["workflow_limits"]["approved"] is False


def test_live_interlock_value_does_not_replace_latched_trigger():
    manager = ExternalInterlockManager()
    manager.activate([_rule()], phase="runtime", latch_runtime=True)
    manager.submit("bms-main", {"sequence": 1, "timestamp_utc": _now(),
                               "health": "ok", "signals": {"pack_voltage_v": 101}})
    assert not manager.signals()[0].safe
    manager.submit("bms-main", {"sequence": 2, "timestamp_utc": _now(),
                               "health": "ok", "signals": {"pack_voltage_v": 90}})
    result = manager.status()["results"][0]
    assert result["latched"] is True
    assert result["value"] == 101
    assert result["current"]["value"] == 90
    assert result["source_sequence"] == 1
    assert result["current"]["source_sequence"] == 2
    assert result["rule"]["maximum"] == 100
    assert not manager.signals()[0].safe


def test_launcher_starts_simulated_service_and_monitor_then_guided_stop(tmp_path, monkeypatch):
    import nhr9300.operator as module
    profile = _profile(tmp_path / "rest.json", duration_s=3)
    path = tmp_path / "service.json"
    path.write_text(json.dumps(_config(tmp_path, profile)), encoding="utf-8")

    def unused_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    port, monitor_port = unused_port(), unused_port()
    args = Namespace(config=path, instrument_id="sim-remote",
                     service_url=f"http://127.0.0.1:{port}", service_python=sys.executable,
                     monitor_port=monitor_port)
    replies = iter(["CONNECT", "PREFLIGHT", "SUPERVISED_WORKFLOW_READY"])
    operator = OperatorConsole(args, ask=lambda _: next(replies), show=lambda _: None)
    operator.selected = "approved-rest-v1"
    children, opened = [], []
    original = module.launch_process

    def launch(argv, log):
        child = original(argv, log)
        children.append(child)
        return child

    monkeypatch.setattr(module, "launch_process", launch)
    monkeypatch.setattr(module.webbrowser, "open", opened.append)
    try:
        operator.launch()
        assert len(children) == 2
        assert opened == [f"http://127.0.0.1:{monitor_port}"]
        operator.preflight()
        operator.start()
        operator.stop()
        snapshot = operator.client.runtime("sim-remote")
        run = snapshot["workflow"].get("last_run") or snapshot["workflow"]
        assert run["state"] == "stopped"
        assert run["recording"]["finalized"]
        assert snapshot["acquisition"]["active"]
    finally:
        # Only the two simulator/monitor children created by this test. No IVI.
        for child in children:
            child.terminate()
            child.wait(timeout=5)


def test_monitor_port_conflict_never_opens_browser(service, monkeypatch):
    import nhr9300.operator as module
    operator = console(service)
    monkeypatch.setattr(module, "port_open", lambda *a: True)
    class WrongMonitor:
        def __enter__(self):
            import io
            return io.StringIO('{"instrument_id":"another", "read_only":true}')
        def __exit__(self, *a):
            pass
    monkeypatch.setattr(module, "urlopen", lambda *a, **kw: WrongMonitor())
    monkeypatch.setattr(module.webbrowser, "open", lambda _: pytest.fail("must not open wrong monitor"))
    with pytest.raises(ValueError, match="occupied"):
        operator.launch_monitor()


def test_manifest_persistence_failure_is_not_finalized(service, monkeypatch):
    import nhr9300.workflow_runs as module
    original = module.atomic_write_json
    def fail_manifest(path, value):
        if path.name == "session-evidence.json":
            raise PermissionError("manifest locked")
        return original(path, value)
    monkeypatch.setattr(module, "atomic_write_json", fail_manifest)
    run = start(service[3], service[1])
    final = service[3].wait_workflow("sim-remote", run["run_id"], timeout_s=10)
    assert final["state"] == "failed"
    assert final["recording"]["finalized"] is False
    assert "files" not in final["recording"]
    assert "manifest locked" in final["recording"]["error"]


def test_hashing_after_verified_cleanup_is_not_a_stop_timeout(service, monkeypatch):
    import nhr9300.workflow_runs as module
    entered, release = threading.Event(), threading.Event()
    original = module.describe_file
    def slow_hash(path, role):
        entered.set()
        assert release.wait(5)
        return original(path, role)
    monkeypatch.setattr(module, "describe_file", slow_hash)
    run = start(service[3], service[1])
    controller = service[2].get("sim-remote").workflow_controller
    try:
        assert entered.wait(5)
        controller._enforce_stop_timeout(run["run_id"], 0.01)
        during = service[3].workflow_run("sim-remote", run["run_id"])
        assert during["state"] == "finalizing"
        assert during["final_safe_state"]["verified"]
        assert not during["emergency_fallback_requested"]
    finally:
        release.set()
    final = service[3].wait_workflow("sim-remote", run["run_id"], timeout_s=10)
    assert final["recording"]["finalized"]
