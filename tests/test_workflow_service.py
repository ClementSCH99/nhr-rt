from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

import nhr9300.execution as execution_module
from nhr9300.client import NHRServiceClient
from nhr9300.errors import NHRError, NHRPolicyError
from nhr9300.service import build_server
from nhr9300.workflow_registry import WorkflowBundle
from nhr9300.workflow_runs import WORKFLOW_ACKNOWLEDGEMENT


def _profile(path: Path, *, duration_s: float = 0.2) -> Path:
    data = {
        "test_description": "approved remote simulation",
        "bench_description": "simulated bench",
        "stop_procedure": "request workflow stop",
        "expected_resource": "sim-remote",
        "expected_serial_number": "SIM-9300",
        "simulation_initial_voltage_v": 90,
        "watchdog_enabled": True,
        "safety_limits": {
            "charge_current": 10,
            "charge_voltage_max": 100,
            "charge_power": 1000,
            "discharge_current": 10,
            "discharge_voltage_min": 80,
            "discharge_power": 1000,
            "approved": True,
            "profile_name": "approved-sim-limits",
        },
        "workflow_limits": {
            "max_current_a": 5,
            "max_power_w": 500,
            "max_stage_duration_s": 10,
            "max_sequence_duration_s": 20,
            "approved": True,
            "profile_name": "approved-sim-workflow",
        },
        "stages": [
            {"name": "rest", "type": "rest", "duration_s": duration_s}
        ],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _config(tmp_path: Path, profile: Path) -> dict:
    bundle = WorkflowBundle.load(profile, hardware=False)
    return {
        "output_dir": str(tmp_path / "runs"),
        "instruments": [
            {
                "id": "sim-remote",
                "backend": "simulator",
                "rate_hz": 10,
                "remote_workflow_control": True,
            }
        ],
        "workflow_registry": [
            {
                "workflow_id": "approved-rest-v1",
                "instrument_id": "sim-remote",
                "profile_path": str(profile),
                "expected_resource": "sim-remote",
                "expected_serial_number": "SIM-9300",
                "expected_bundle_digest": bundle.digest,
                "approved": True,
            }
        ],
    }


def _start_server(tmp_path: Path, *, duration_s: float = 0.2):
    profile = _profile(tmp_path / "workflow.json", duration_s=duration_s)
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return (
        profile,
        config,
        server,
        manager,
        thread,
        NHRServiceClient(f"http://{host}:{port}"),
    )


def _wait_terminal(client: NHRServiceClient, run_id: str) -> dict:
    deadline = time.monotonic() + 5
    while True:
        state = client.workflow_run("sim-remote", run_id)
        if state["state"] in {"passed", "stopped", "failed", "interrupted"}:
            return state
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_bundle_detects_bytes_changed_after_startup(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json")
    bundle = WorkflowBundle.load(profile, hardware=False)

    profile.write_bytes(profile.read_bytes() + b"\n")

    with pytest.raises(NHRPolicyError, match="changed after service startup"):
        bundle.verify_unchanged()


def test_bundle_digest_covers_referenced_dynamic_csv_bytes(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json")
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["stages"] = [
        {
            "name": "dynamic",
            "type": "csv_profile",
            "duration_s": 0.2,
            "csv_path": "profile.csv",
            "profile_kind": "current",
            "current_a": 5,
            "power_w": 500,
            "voltage_limit_enabled": True,
            "current_limit_enabled": True,
            "power_limit_enabled": True,
            "charge_voltage_limit_v": 98,
            "discharge_voltage_limit_v": 82,
        }
    ]
    profile.write_text(json.dumps(data), encoding="utf-8")
    dynamic = tmp_path / "profile.csv"
    dynamic.write_text("time_s,current_a\n0,1\n0.2,0\n", encoding="utf-8")
    bundle = WorkflowBundle.load(profile, hardware=False)

    dynamic.write_text("time_s,current_a\n0,2\n0.2,0\n", encoding="utf-8")

    with pytest.raises(NHRPolicyError, match="changed after service startup"):
        bundle.verify_unchanged()


def test_approved_workflow_api_preflights_runs_and_is_idempotent(tmp_path) -> None:
    profile, config, server, manager, thread, client = _start_server(tmp_path)
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    try:
        workflows = client.workflows("sim-remote")
        assert workflows[0]["workflow_id"] == "approved-rest-v1"
        assert workflows[0]["bundle_digest"] == digest
        assert workflows[0]["available"] is True

        with pytest.raises(NHRError, match="Unknown workflow request fields"):
            client._request(
                "POST",
                "/instruments/sim-remote/workflow-runs/preflight",
                {
                    "workflow_id": "approved-rest-v1",
                    "bundle_digest": digest,
                    "profile": {"stages": []},
                },
            )

        preflight = client.preflight_workflow(
            "sim-remote", "approved-rest-v1", digest
        )
        assert preflight["passed"] is True
        assert preflight["checks"]["final_safe_state_verified"] is True

        request_id = str(uuid.uuid4())
        first = client.start_workflow(
            "sim-remote",
            request_id=request_id,
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        repeated = client.start_workflow(
            "sim-remote",
            request_id=request_id,
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        assert repeated["run_id"] == first["run_id"]

        final = _wait_terminal(client, first["run_id"])
        assert final["state"] == "passed"
        assert final["final_safe_state"] == {
            "verified": True,
            "reconnected": True,
        }
        assert Path(final["report_path"]).is_file()
        assert manager.get("sim-remote").collector.running is True
        profile.write_bytes(profile.read_bytes() + b"\n")
        after_drift = client.start_workflow(
            "sim-remote",
            request_id=request_id,
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        assert after_drift["run_id"] == first["run_id"]
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_workflow_exclusivity_and_idempotent_stop(tmp_path) -> None:
    profile, config, server, manager, thread, client = _start_server(
        tmp_path, duration_s=2.0
    )
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    try:
        first = client.start_workflow(
            "sim-remote",
            request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        with pytest.raises(NHRError, match="active operation"):
            client.start_workflow(
                "sim-remote",
                request_id=str(uuid.uuid4()),
                workflow_id="approved-rest-v1",
                bundle_digest=digest,
            )
        deadline = time.monotonic() + 2
        while True:
            running = client.workflow_run("sim-remote", first["run_id"])
            if running["state"] == "running" and running["step"] is not None:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert running["stage"]["name"] == "rest"
        assert running["progress"]["kind"] == "elapsed_time"
        assert running["progress"]["percent"] is not None
        with pytest.raises(NHRError, match="unavailable"):
            client.command("sim-remote", "disable")

        stopped = client.stop_workflow("sim-remote", first["run_id"])
        assert stopped["stop_requested"] is True
        repeated = client.stop_workflow("sim-remote", first["run_id"])
        assert repeated["run_id"] == first["run_id"]
        final = _wait_terminal(client, first["run_id"])
        assert final["state"] == "stopped"
        assert final["final_safe_state"]["verified"] is True
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_registry_drift_is_rejected_by_service(tmp_path) -> None:
    profile, config, server, manager, thread, client = _start_server(tmp_path)
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    try:
        with pytest.raises(NHRError, match="digest does not match"):
            client.preflight_workflow(
                "sim-remote", "approved-rest-v1", "sha256:" + "0" * 64
            )
        profile.write_bytes(profile.read_bytes() + b"\n")
        with pytest.raises(NHRError, match="changed after service startup"):
            client.preflight_workflow("sim-remote", "approved-rest-v1", digest)
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_physical_start_requires_acknowledgement_and_approved_timeout(
    tmp_path,
) -> None:
    profile = _profile(tmp_path / "workflow.json")
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["expected_resource"] = "DC PM 1"
    data["expected_serial_number"] = "79503"
    profile.write_text(json.dumps(data), encoding="utf-8")
    bundle = WorkflowBundle.load(profile, hardware=True)
    config = {
        "output_dir": str(tmp_path / "runs"),
        "instruments": [
            {
                "id": "nhr-79503",
                "backend": "ivi",
                "logical_name": "DC PM 1",
                "remote_workflow_control": True,
            }
        ],
        "workflow_registry": [
            {
                "workflow_id": "approved-hardware-v1",
                "instrument_id": "nhr-79503",
                "profile_path": str(profile),
                "expected_resource": "DC PM 1",
                "expected_serial_number": "79503",
                "expected_bundle_digest": bundle.digest,
                "approved": True,
            }
        ],
    }
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    request = {
        "instrument_id": "nhr-79503",
        "request_id": str(uuid.uuid4()),
        "workflow_id": "approved-hardware-v1",
        "bundle_digest": bundle.digest,
    }
    try:
        with pytest.raises(NHRError, match="operator acknowledgement"):
            client.start_workflow(**request)
        request["request_id"] = str(uuid.uuid4())
        request["operator_acknowledgement"] = WORKFLOW_ACKNOWLEDGEMENT
        with pytest.raises(NHRError, match="controlled-stop timeout"):
            client.start_workflow(**request)
        assert manager.get("nhr-79503").instrument.connected is False
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_remote_workflow_control_is_disabled_by_default(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json")
    config = _config(tmp_path, profile)
    config["instruments"][0].pop("remote_workflow_control")
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    try:
        with pytest.raises(NHRError, match="disabled"):
            client.preflight_workflow("sim-remote", "approved-rest-v1", digest)
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_interrupted_manifest_blocks_start_until_preflight(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json")
    config = _config(tmp_path, profile)
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    prior_id = str(uuid.uuid4())
    prior_dir = tmp_path / "runs" / "workflow-runs" / prior_id
    prior_dir.mkdir(parents=True)
    (prior_dir / "run-state.json").write_text(
        json.dumps(
            {
                "run_id": prior_id,
                "request_id": str(uuid.uuid4()),
                "workflow_id": "approved-rest-v1",
                "bundle_digest": digest,
                "instrument_id": "sim-remote",
                "state": "running",
            }
        ),
        encoding="utf-8",
    )
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        recovered = client.workflow_run("sim-remote", prior_id)
        assert recovered["state"] == "interrupted"
        assert recovered["final_safe_state"]["verified"] is False
        with pytest.raises(NHRError, match="successful preflight"):
            client.start_workflow(
                "sim-remote",
                request_id=str(uuid.uuid4()),
                workflow_id="approved-rest-v1",
                bundle_digest=digest,
            )
        assert client.preflight_workflow(
            "sim-remote", "approved-rest-v1", digest
        )["passed"]
        started = client.start_workflow(
            "sim-remote",
            request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        assert _wait_terminal(client, started["run_id"])["state"] == "passed"
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_separate_64_bit_process_completes_workflow_lifecycle(tmp_path) -> None:
    profile, config, server, manager, thread, client = _start_server(tmp_path)
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    host, port = server.server_address
    code = """
import json
import struct
import sys
import time
import uuid
from nhr9300.client import NHRServiceClient

assert struct.calcsize('P') * 8 == 64
client = NHRServiceClient(sys.argv[1])
digest = sys.argv[2]
assert client.preflight_workflow('sim-remote', 'approved-rest-v1', digest)['passed']
run = client.start_workflow(
    'sim-remote',
    request_id=str(uuid.uuid4()),
    workflow_id='approved-rest-v1',
    bundle_digest=digest,
)
deadline = time.monotonic() + 5
while True:
    state = client.workflow_run('sim-remote', run['run_id'])
    if state['state'] in {'passed', 'stopped', 'failed', 'interrupted'}:
        break
    assert time.monotonic() < deadline
    time.sleep(0.02)
print(json.dumps({'bits': 64, 'state': state['state']}))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path("src").resolve())
    try:
        try:
            completed = subprocess.run(
                [
                    "py",
                    "-3.12",
                    "-c",
                    code,
                    f"http://{host}:{port}",
                    digest,
                ],
                cwd=Path.cwd(),
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            pytest.skip("Windows 64-bit Python launcher is unavailable")
        result = json.loads(completed.stdout)
        assert result == {"bits": 64, "state": "passed"}
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_workflow_failure_converges_on_verified_safe_state(
    tmp_path, monkeypatch
) -> None:
    profile, config, server, manager, thread, client = _start_server(tmp_path)
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    monkeypatch.setattr(
        execution_module.SequenceRunner,
        "run",
        lambda self, stages: (_ for _ in ()).throw(RuntimeError("forced failure")),
    )
    try:
        run = client.start_workflow(
            "sim-remote",
            request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        final = _wait_terminal(client, run["run_id"])
        assert final["state"] == "failed"
        assert "forced failure" in final["error"]
        assert final["final_safe_state"]["verified"] is True
        backend = manager.get("sim-remote").instrument._backend
        assert backend.enabled is False
        assert backend.watchdog_enabled is False
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_service_shutdown_stops_active_approved_workflow(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json", duration_s=2.0)
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    managed = manager.get("sim-remote")
    controller = managed.workflow_controller
    assert controller is not None
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    run, _ = controller.start(
        request_id=str(uuid.uuid4()),
        workflow_id="approved-rest-v1",
        bundle_digest=digest,
        operator_acknowledgement="",
    )

    manager.close()

    final = controller.get(run["run_id"])
    backend = managed.instrument._backend
    assert final["state"] == "stopped"
    assert final["final_safe_state"]["verified"] is True
    assert backend.connected is False
    assert backend.enabled is False
    assert backend.watchdog_enabled is False
    server.server_close()
