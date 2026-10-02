from __future__ import annotations

import csv
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import nhr9300.execution as execution_module
import nhr9300.routines as routines_module
import nhr9300.sequences as sequences_module
import nhr9300.workflow_runs as workflow_runs_module
from nhr9300.client import NHRServiceClient
from nhr9300.errors import NHRError, NHRPolicyError
from nhr9300.service import build_server
from nhr9300.types import OperatingState
from nhr9300.workflow_registry import WorkflowBundle, WorkflowRegistry
from nhr9300.workflow_runs import WORKFLOW_ACKNOWLEDGEMENT


def _profile(
    path: Path,
    *,
    duration_s: float = 0.2,
    post_sequence_rest_s: float = 0.0,
) -> Path:
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
            "post_sequence_rest_s": post_sequence_rest_s,
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


def test_operator_ends_discharge_stage_and_recording_continues_into_rest(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json", duration_s=3.0)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["stages"] = [
        {
            "name": "power-discharge", "type": "constant_power",
            "mode": "discharge", "duration_s": 3.0,
            "current_a": 5.0, "voltage_v": 82.0, "power_w": 200.0,
            "voltage_limit_enabled": True, "current_limit_enabled": True,
            "power_limit_enabled": True,
        },
        {"name": "cooldown-rest", "type": "rest", "duration_s": 1.5},
    ]
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        started = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        run_id = started["run_id"]
        deadline = time.monotonic() + 5
        while True:
            snapshot = client.workflow_run("sim-remote", run_id)
            if (snapshot.get("stage") or {}).get("index") == 0 and (snapshot.get("step") or "").endswith("_wait"):
                break
            assert time.monotonic() < deadline, snapshot
            time.sleep(0.02)

        intervention = client.end_workflow_stage("sim-remote", run_id, 0)
        repeated = client.end_workflow_stage("sim-remote", run_id, 0)
        assert repeated["intervention_id"] == intervention["intervention_id"]
        with pytest.raises(NHRError, match="Expected stage"):
            client.end_workflow_stage("sim-remote", run_id, 1)
        detail = client.explain_workflow_stage_end(
            "sim-remote", run_id, intervention["intervention_id"],
            "Cooling was not started",
        )
        assert detail["reason_detail"] == "Cooling was not started"
        assert client.explain_workflow_stage_end(
            "sim-remote", run_id, intervention["intervention_id"],
            "Cooling was not started",
        )["reason_detail"] == "Cooling was not started"
        with pytest.raises(NHRError, match="already recorded"):
            client.explain_workflow_stage_end(
                "sim-remote", run_id, intervention["intervention_id"],
                "Different reason",
            )

        final = _wait_terminal(client, run_id)
        assert final["state"] == "passed", final
        assert final["recording"]["finalized"] is True
        assert final["operator_interventions"][0]["status"] == "applied"
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        assert report["operator_intervention"] is True
        assert report["operator_interventions"][0]["reason_detail"] == "Cooling was not started"
        stages = report["sequence_result"]["stages"]
        assert stages[0]["termination_reason"] == "operator_stage_end"
        assert stages[1]["state"] == "passed"
        assert report["sequence_result"]["state"] == "passed"
        with Path(final["recording"]["path"]).open(newline="", encoding="utf-8") as handle:
            session_rows = list(csv.DictReader(handle))
        assert session_rows
        assert {row["routine_id"] for row in session_rows} >= {
            stages[0]["routine_id"], stages[1]["routine_id"]
        }
        assert manager.get("sim-remote").collector.running is True
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_stage_end_readback_failure_does_not_start_next_stage(tmp_path, monkeypatch) -> None:
    profile = _profile(
        tmp_path / "workflow.json", duration_s=3.0, post_sequence_rest_s=0.4
    )
    config = _config(tmp_path, profile)
    server, _manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        started = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        run_id = started["run_id"]
        deadline = time.monotonic() + 5
        while True:
            snapshot = client.workflow_run("sim-remote", run_id)
            if (snapshot.get("stage") or {}).get("index") == 0 and (snapshot.get("step") or "").endswith("_wait"):
                break
            assert time.monotonic() < deadline, snapshot
            time.sleep(0.02)

        def unsafe_readback(_status):
            raise RuntimeError("Injected unsafe stage transition")

        monkeypatch.setattr(sequences_module, "require_disabled_inactive", unsafe_readback)
        client.end_workflow_stage("sim-remote", run_id, 0)
        final = _wait_terminal(client, run_id)
        assert final["state"] == "failed"
        assert final["operator_interventions"][0]["status"] == "not_applied"
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        assert "Injected unsafe stage transition" in report["error"]
        assert "sequence_result" not in report
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_bundle_detects_bytes_changed_after_startup(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json")
    bundle = WorkflowBundle.load(profile, hardware=False)

    profile.write_bytes(profile.read_bytes() + b"\n")

    with pytest.raises(NHRPolicyError, match="changed after service startup"):
        bundle.verify_unchanged()


def test_invalid_workflow_limits_do_not_abort_registry_startup(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json")
    config = _config(tmp_path, profile)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["workflow_limits"]["max_voltage_v"] = 100
    profile.write_text(json.dumps(data), encoding="utf-8")

    registry = WorkflowRegistry.from_config(
        config,
        base_dir=tmp_path,
        instrument_backends={"sim-remote": "simulator"},
    )

    entry = registry.list_for("sim-remote")[0]
    assert entry["available"] is False
    assert entry["error"].startswith("NHRValidationError: Invalid workflow limits")


def test_bundle_digest_covers_referenced_dynamic_csv_bytes(tmp_path) -> None:
    profile = _profile(tmp_path / "workflow.json", post_sequence_rest_s=0.1)
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
        assert preflight["recording"]["finalized"] is True
        assert Path(preflight["recording"]["path"]).name == "preflight.csv"
        assert Path(preflight["recording"]["path"]).parent.name == "measurements"
        preflight_report = json.loads(
            Path(preflight["report_path"]).read_text(encoding="utf-8")
        )
        preflight_manifest = json.loads(
            Path(preflight_report["artifact_manifest_path"]).read_text(
                encoding="utf-8"
            )
        )
        assert "preflight_measurements" in {
            item["role"] for item in preflight_manifest["artifacts"]
        }
        assert preflight_report["simulator_initial_state"] == {
            "policy": "reset_from_profile",
            "configured_voltage_v": 90.0,
            "actual_initial_voltage_v": 90.0,
        }
        assert client.configuration()["instruments"][0][
            "simulator_initial_state_policy"
        ] == "reset_from_profile"
        backend = manager.get("sim-remote").instrument._backend
        assert backend.initial_voltage_v == 90.0
        assert backend._voltage_v == pytest.approx(90.0)

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
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        manifest = json.loads(
            Path(report["artifact_manifest_path"]).read_text(encoding="utf-8")
        )
        assert {item["role"] for item in manifest["artifacts"]} >= {
            "report",
            "workflow_profile",
            "workflow_sequence",
            "workflow_stage",
        }
        assert Path(report["sequence_result"]["global_csv_path"]).name == "sequence.csv"
        assert Path(report["sequence_result"]["global_csv_path"]).parent.name == "measurements"
        assert all(
            Path(stage["csv_path"]).parent.name == "stages"
            for stage in report["sequence_result"]["stages"]
        )
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


def test_sop_csv_changes_keep_approved_point_authority(tmp_path) -> None:
    profile = _sop_profile(tmp_path / "workflow.json", duration_s=2.5)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["workflow_limits"]["arm_lease_renewal_enabled"] = True
    stage = data["stages"][0]
    stage.update(type="csv_profile", csv_path="profile.csv", profile_kind="current",
                 charge_voltage_limit_v=100, discharge_voltage_limit_v=80)
    stage.pop("mode")
    stage.pop("voltage_v")
    (tmp_path / "profile.csv").write_text(
        "time_s,current_a\n0,4\n1.5,4\n2.5,0\n", encoding="utf-8"
    )
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        _publish_sop(client, 1, 200.0)
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        controller = manager.get("sim-remote").workflow_controller
        assert controller is not None
        deadline = time.monotonic() + 5
        while controller.active_sop_limit() is None:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        _publish_sop(client, 2, 100.0)
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        assert final["state"] == "passed", final
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        decisions = report["arm_lease"]["events"]
        assert [event["point_index"] for event in decisions
                if event["decision"] == "profile_point_applied"] == [0, 1]
        assert any(event["decision"] == "sop_limit_applied" and
                   event["applied_power_w"] == 100.0 for event in decisions)
        assert report["sop_control"]["fault"] is None
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()

def test_post_sequence_rest_is_recorded_in_sequence_and_own_stage(tmp_path) -> None:
    profile = _profile(
        tmp_path / "workflow.json",
        duration_s=0.1,
        post_sequence_rest_s=0.3,
    )
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        run = client.start_workflow(
            "sim-remote",
            request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        final = client.wait_workflow(
            "sim-remote",
            run["run_id"],
            timeout_s=10,
            poll_interval_s=0.05,
        )
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        stages = report["sequence_result"]["stages"]
        assert [stage["routine_id"] for stage in stages]
        assert len(stages) == 2
        assert report["sequence_result"]["stage_sample_counts"][1] >= 2
        post_rest_path = Path(stages[1]["csv_path"])
        assert post_rest_path.name == "02-post-sequence-rest.csv"
        with post_rest_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert rows
        assert {row["state"] for row in rows}.isdisjoint({"charge", "discharge"})
        assert final["recording"]["finalized"] is True
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
        assert stopped["stop_accepted"] is True
        assert stopped["already_requested"] is False
        repeated = client.stop_workflow("sim-remote", first["run_id"])
        assert repeated["run_id"] == first["run_id"]
        assert repeated["stop_accepted"] is False
        assert repeated["already_requested"] is True
        final = _wait_terminal(client, first["run_id"])
        assert final["state"] == "stopped"
        assert final["final_safe_state"]["verified"] is True
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_workflow_preflight_uses_longer_configurable_client_timeout(monkeypatch) -> None:
    observed: list[float] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return b'{"passed": true}'

    def open_request(request, *, timeout):
        observed.append(timeout)
        return Response()

    monkeypatch.setattr("nhr9300.client.urlopen", open_request)
    client = NHRServiceClient("http://127.0.0.1:9300")

    assert client.preflight_workflow("nhr", "rest", "sha256:digest")["passed"]
    assert client.preflight_workflow(
        "nhr", "rest", "sha256:digest", timeout_s=45.0
    )["passed"]
    assert observed == [30.0, 45.0]


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
        assert recovered["recording"]["path"] == str(
            (prior_dir / "measurements" / "session.csv").resolve()
        )
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
print(json.dumps({
    'bits': 64,
    'state': state['state'],
    'error': state.get('error'),
    'report_path': state.get('report_path'),
}))
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
        assert result["bits"] == 64
        assert result["state"] == "passed", result
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
        assert final["recording"]["finalized"] is True
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


def test_service_owned_renewals_are_durable_and_finish_safe(
    tmp_path, monkeypatch
) -> None:
    profile = _profile(tmp_path / "workflow.json", duration_s=2.0)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["workflow_limits"]["arm_lease_renewal_enabled"] = True
    data["stages"] = [
        {
            "name": "accelerated-long-cc",
            "type": "constant_current",
            "duration_s": 2.0,
            "mode": "charge",
            "current_a": 2,
            "voltage_v": 99,
            "voltage_limit_enabled": True,
            "current_limit_enabled": True,
            "power_limit_enabled": False,
        }
    ]
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    original_supervisor = workflow_runs_module.ArmLeaseSupervisor

    def accelerated_supervisor(**kwargs):
        return original_supervisor(
            **kwargs,
            renewal_margin_s=301,
            renewal_threshold_s=0.1,
        )

    monkeypatch.setattr(
        workflow_runs_module, "ArmLeaseSupervisor", accelerated_supervisor
    )
    server, manager = build_server(config, port=0, announce=False)
    managed = manager.get("sim-remote")
    original_arm = managed.instrument.arm
    monkeypatch.setattr(managed.instrument, "arm", lambda _duration: original_arm(1.0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    try:
        run = client.start_workflow(
            "sim-remote",
            request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        assert client.detach_observer("sim-remote")["service_connected"] is True
        final = _wait_terminal(client, run["run_id"])
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        artifacts = json.loads(
            Path(final["report_path"])
            .with_name("artifacts.json")
            .read_text(encoding="utf-8")
        )
        decisions = [
            item["decision"] for item in report["arm_lease"]["events"]
        ]

        assert final["state"] == "passed"
        assert final["arm_lease"]["renewal_count"] == 1
        assert decisions.count("renewed") == final["arm_lease"]["renewal_count"]
        assert artifacts["arm_lease"] == report["arm_lease"]
        assert final["final_safe_state"]["verified"] is True
        backend = manager.get("sim-remote").instrument._backend
        assert backend.enabled is False
        assert backend.watchdog_enabled is False
        assert backend.setpoints == backend.setpoints.__class__()
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_approved_duration_overrun_uses_controlled_then_emergency_stop(
    tmp_path, monkeypatch
) -> None:
    profile = _profile(tmp_path / "workflow.json", duration_s=0.05)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["workflow_limits"]["arm_lease_renewal_enabled"] = True
    data["stages"] = [
        {
            "name": "blocked-long-cc",
            "type": "constant_current",
            "duration_s": 0.05,
            "mode": "charge",
            "current_a": 2,
            "voltage_v": 99,
            "voltage_limit_enabled": True,
            "current_limit_enabled": True,
            "power_limit_enabled": False,
        }
    ]
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    original_supervisor = workflow_runs_module.ArmLeaseSupervisor

    def accelerated_supervisor(**kwargs):
        return original_supervisor(**kwargs, renewal_threshold_s=0.01)

    monkeypatch.setattr(
        workflow_runs_module, "ArmLeaseSupervisor", accelerated_supervisor
    )
    monkeypatch.setattr(
        routines_module.WaitStep,
        "execute",
        lambda self, context: time.sleep(2.0),
    )
    server, manager = build_server(config, port=0, announce=False)
    managed = manager.get("sim-remote")
    managed.workflow_controller.controlled_stop_timeout_s = 0.05
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    try:
        run = client.start_workflow(
            "sim-remote",
            request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        final = _wait_terminal(client, run["run_id"])
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))

        assert final["state"] == "stopped"
        assert final["stop_cause"]["origin"] == "approved_duration_limit"
        assert final["emergency_fallback_requested"] is True
        assert report["stop_cause"]["origin"] == "approved_duration_limit"
        assert any(
            item["reason"] == "approved_stage_duration_exceeded"
            for item in report["arm_lease"]["events"]
        )
        assert final["final_safe_state"]["verified"] is True
        assert managed.instrument._backend.enabled is False
        assert managed.instrument._backend.watchdog_enabled is False
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_renewal_failure_requests_controlled_stop_and_preserves_cause(
    tmp_path, monkeypatch
) -> None:
    profile = _profile(tmp_path / "workflow.json", duration_s=8.0)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["workflow_limits"]["arm_lease_renewal_enabled"] = True
    data["stages"] = [
        {
            "name": "failing-renewal-cc",
            "type": "constant_current",
            "duration_s": 8.0,
            "mode": "charge",
            "current_a": 2,
            "voltage_v": 99,
            "voltage_limit_enabled": True,
            "current_limit_enabled": True,
            "power_limit_enabled": False,
        }
    ]
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    original_supervisor = workflow_runs_module.ArmLeaseSupervisor

    def accelerated_supervisor(**kwargs):
        return original_supervisor(
            **kwargs,
            renewal_margin_s=301,
            renewal_threshold_s=0.1,
        )

    monkeypatch.setattr(
        workflow_runs_module, "ArmLeaseSupervisor", accelerated_supervisor
    )
    server, manager = build_server(config, port=0, announce=False)
    managed = manager.get("sim-remote")
    original_arm = managed.instrument.arm
    monkeypatch.setattr(managed.instrument, "arm", lambda _duration: original_arm(1.0))
    monkeypatch.setattr(
        managed.instrument,
        "renew_arm",
        lambda _duration: (_ for _ in ()).throw(RuntimeError("forced failure")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    try:
        run = client.start_workflow(
            "sim-remote",
            request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        # Renewal is forced at 0.1 s. Leave the independent stage-duration
        # guard enough headroom for Windows evidence I/O so this test exercises
        # renewal failure rather than an unrelated scheduling/duration overrun.
        final = client.wait_workflow(
            "sim-remote", run["run_id"], timeout_s=20, poll_interval_s=0.1
        )
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))

        assert final["state"] == "stopped"
        assert final["stop_cause"]["origin"] == "arm_lease_renewal"
        assert report["stop_cause"]["origin"] == "arm_lease_renewal"
        assert any(
            item["decision"] == "refused" and "forced failure" in item["reason"]
            for item in report["arm_lease"]["events"]
        )
        assert final["final_safe_state"]["verified"] is True
        assert managed.instrument._backend.enabled is False
        assert managed.instrument._backend.watchdog_enabled is False
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_actual_lease_expiration_requests_immediate_emergency_stop(
    tmp_path, monkeypatch
) -> None:
    profile = _profile(tmp_path / "workflow.json", duration_s=0.4)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["workflow_limits"]["arm_lease_renewal_enabled"] = True
    data["stages"] = [
        {
            "name": "expired-lease-cc",
            "type": "constant_current",
            "duration_s": 0.4,
            "mode": "charge",
            "current_a": 2,
            "voltage_v": 99,
            "voltage_limit_enabled": True,
            "current_limit_enabled": True,
            "power_limit_enabled": False,
        }
    ]
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    original_supervisor = workflow_runs_module.ArmLeaseSupervisor

    class ExpiringSupervisor(original_supervisor):
        expired = False

        def checkpoint(self, measurement):
            if not self.expired:
                self.expired = True
                self.instrument._armed_until = time.monotonic() - 1
            return super().checkpoint(measurement)

    def accelerated_supervisor(**kwargs):
        return ExpiringSupervisor(**kwargs, renewal_threshold_s=0.1)

    monkeypatch.setattr(
        workflow_runs_module, "ArmLeaseSupervisor", accelerated_supervisor
    )
    server, manager = build_server(config, port=0, announce=False)
    managed = manager.get("sim-remote")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    digest = config["workflow_registry"][0]["expected_bundle_digest"]
    try:
        run = client.start_workflow(
            "sim-remote",
            request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=digest,
        )
        final = _wait_terminal(client, run["run_id"])
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        artifacts = json.loads(
            Path(final["report_path"])
            .with_name("artifacts.json")
            .read_text(encoding="utf-8")
        )

        assert final["state"] == "stopped"
        assert final["stop_cause"]["origin"] == "arm_lease_expired"
        assert final["emergency_fallback_requested"] is True
        assert report["stop_cause"]["origin"] == "arm_lease_expired"
        assert any(
            item["decision"] == "expired"
            for item in report["arm_lease"]["events"]
        )
        assert artifacts["arm_lease"] == report["arm_lease"]
        assert final["final_safe_state"]["verified"] is True
        assert managed.instrument._backend.enabled is False
        assert managed.instrument._backend.watchdog_enabled is False
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def _sop_profile(path: Path, *, duration_s: float = 3.0) -> Path:
    _profile(path, post_sequence_rest_s=0.2)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sop_control"] = {
        "source_id": "bms-main", "charge_signal": "charge_sop_w",
        "discharge_signal": "discharge_sop_w", "max_age_s": 5.0,
        "min_charge_w": 50.0, "min_discharge_w": 50.0, "low_cycles": 2,
    }
    data["stages"] = [
        {"name": "charge", "type": "constant_current", "duration_s": duration_s,
         "mode": "charge", "current_a": 5, "voltage_v": 100, "power_w": 500,
         "voltage_limit_enabled": True, "current_limit_enabled": True,
         "power_limit_enabled": True},
        {"name": "later", "type": "rest", "duration_s": 0.2},
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _publish_sop(client: NHRServiceClient, sequence: int, charge_w: float) -> None:
    client.submit_external_snapshot(
        "sim-remote", "bms-main", sequence=sequence,
        timestamp_utc=datetime.now(timezone.utc).isoformat(), health="ok",
        signals={"charge_sop_w": charge_w, "discharge_sop_w": 300.0},
    )


def test_sop_zero_jumps_to_final_rest_and_records_stopped_outcome(tmp_path) -> None:
    profile = _sop_profile(tmp_path / "workflow.json")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        _publish_sop(client, 1, 300.0)
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        deadline = time.monotonic() + 5
        instrument = manager.get("sim-remote").instrument
        while not instrument.connected or instrument.read_status().state.name != "CHARGE":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        _publish_sop(client, 2, 0.0)
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        assert final["state"] == "stopped"
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        assert report["outcome"] == "stopped"
        assert report["final_safe_state_verified"] is True
        assert report["sop_control"]["fault"]["reason"] == "sop_zero_or_negative"
        assert final["stop_cause"]["origin"] == "sop_control"
        assert report["sequence_result"]["skipped_stages"] == ["later"]
        assert len(report["sequence_result"]["stages"]) == 2
        assert report["sequence_result"]["stages"][-1]["state"] == "passed"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


def test_sop_zero_blocks_start_before_output_can_be_enabled(tmp_path) -> None:
    profile = _sop_profile(tmp_path / "workflow.json")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        _publish_sop(client, 1, 0.0)
        with pytest.raises(NHRError, match="sop_zero_or_negative"):
            client.start_workflow(
                "sim-remote", request_id=str(uuid.uuid4()),
                workflow_id="approved-rest-v1",
                bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
            )
        assert manager.get("sim-remote").instrument.connected is False
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


def test_sop_caps_current_mode_in_simulator(tmp_path) -> None:
    profile = _sop_profile(tmp_path / "workflow.json", duration_s=1.3)
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        _publish_sop(client, 1, 100.0)
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        assert final["state"] == "passed"
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        applied = [event for event in report["sop_control"]["events"]
                   if event["kind"] == "limit_applied"]
        assert applied and applied[0]["applied_power_w"] == 100.0
        with Path(report["sequence_result"]["global_csv_path"]).open(
            encoding="utf-8", newline=""
        ) as handle:
            samples = [row for row in csv.DictReader(handle) if row["state"] == "charge"]
        assert samples
        assert max(float(row["power_w"]) for row in samples) <= 100.01
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


def test_sop_profile_requires_terminal_rest_and_power_channel(tmp_path) -> None:
    profile = _sop_profile(tmp_path / "workflow.json")
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["workflow_limits"]["post_sequence_rest_s"] = 0
    profile.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(NHRError, match="post_sequence_rest_s"):
        WorkflowBundle.load(profile, hardware=False)
    data["workflow_limits"]["post_sequence_rest_s"] = 0.2
    data["stages"][0]["power_limit_enabled"] = False
    profile.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(NHRError, match="power_limit_enabled"):
        WorkflowBundle.load(profile, hardware=False)


def test_sop_reduction_and_restoration_stay_within_approved_ceiling(tmp_path) -> None:
    profile = _sop_profile(tmp_path / "workflow.json", duration_s=4.5)
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        _publish_sop(client, 1, 200.0)
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        controller = manager.get("sim-remote").workflow_controller
        assert controller is not None

        def wait_limit(expected: float) -> None:
            deadline = time.monotonic() + 3
            while True:
                current = controller.active_sop_limit()
                if current is not None and current["applied_w"] == expected:
                    return
                assert time.monotonic() < deadline
                time.sleep(0.02)

        wait_limit(200.0)
        _publish_sop(client, 2, 100.0)
        wait_limit(100.0)
        _publish_sop(client, 3, 700.0)
        wait_limit(500.0)
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        assert final["state"] == "passed"
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        applied = [event["applied_power_w"] for event in report["sop_control"]["events"]
                   if event["kind"] == "limit_applied"]
        assert applied[:3] == [200.0, 100.0, 500.0]
        assert report["sop_control"]["fault"] is None
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


@pytest.mark.parametrize("stage_type", ["cccv", "constant_power", "csv_profile"])
def test_sop_caps_other_regulation_modes(tmp_path, stage_type: str) -> None:
    profile = _sop_profile(tmp_path / "workflow.json", duration_s=1.2)
    data = json.loads(profile.read_text(encoding="utf-8"))
    stage = data["stages"][0]
    stage["type"] = stage_type
    if stage_type == "cccv":
        stage["voltage_v"] = 90
        stage["cutoff_current_a"] = 1.2
    elif stage_type == "csv_profile":
        stage.pop("mode")
        stage.pop("voltage_v")
        stage["csv_path"] = "profile.csv"
        stage["profile_kind"] = "current"
        stage["charge_voltage_limit_v"] = 100
        stage["discharge_voltage_limit_v"] = 80
        (tmp_path / "profile.csv").write_text(
            "time_s,current_a\n0,4\n1.2,0\n", encoding="utf-8"
        )
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        source_limit = 700.0 if stage_type == "cccv" else 100.0
        _publish_sop(client, 1, source_limit)
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        assert final["state"] == "passed"
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        events = [event for event in report["sop_control"]["events"]
                  if event["kind"] == "limit_applied"]
        expected_limit = 500.0 if stage_type == "cccv" else 100.0
        assert events and events[0]["applied_power_w"] == expected_limit
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


@pytest.mark.parametrize("fault", ["low", "stale"])
@pytest.mark.parametrize("off_readback", [False, True])
def test_sop_low_or_stale_finishes_in_final_rest(
    tmp_path, monkeypatch, fault: str, off_readback: bool
) -> None:
    profile = _sop_profile(tmp_path / "workflow.json", duration_s=4.0)
    data = json.loads(profile.read_text(encoding="utf-8"))
    if fault == "stale":
        data["sop_control"]["max_age_s"] = 1.7
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    if off_readback:
        backend = manager.get("sim-remote").instrument._backend
        original_read_status = backend.read_status

        def read_status_with_off():
            status = original_read_status()
            if not status.enabled and status.state == OperatingState.STANDBY:
                return replace(
                    status, state=OperatingState.OFF,
                    setpoints=replace(status.setpoints, state=OperatingState.OFF),
                )
            return status

        monkeypatch.setattr(backend, "read_status", read_status_with_off)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        _publish_sop(client, 1, 300.0)
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        if fault == "low":
            instrument = manager.get("sim-remote").instrument
            deadline = time.monotonic() + 5
            while not instrument.connected or instrument.read_status().state.name != "CHARGE":
                assert time.monotonic() < deadline
                time.sleep(0.02)
            _publish_sop(client, 2, 25.0)
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        assert final["state"] == "stopped"
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        expected = "sop_below_minimum" if fault == "low" else "signal_stale"
        assert report["sop_control"]["fault"]["reason"] == expected
        if fault == "low":
            assert report["sop_control"]["fault"]["low_cycles"] == 2
        assert report["final_safe_state_verified"] is True
        assert report["emergency_fallback_used"] is False
        assert report["sequence_result"]["stages"][-1]["state"] == "passed"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


@pytest.mark.parametrize("stage_type", ["constant_current", "constant_power", "cccv"])
def test_unmet_condition_at_duration_runs_final_rest_without_failure(tmp_path, stage_type) -> None:
    profile = _profile(tmp_path / "workflow.json", post_sequence_rest_s=0.2)
    data = json.loads(profile.read_text(encoding="utf-8"))
    stage = {
        "name": "bounded-test", "type": stage_type, "duration_s": 0.4,
        "voltage_limit_enabled": True, "current_limit_enabled": True,
        "power_limit_enabled": True,
    }
    if stage_type == "csv_profile":
        (tmp_path / "profile.csv").write_text(
            "time_s,current_a\n0,2\n0.4,0\n", encoding="utf-8"
        )
        stage.update(csv_path="profile.csv", profile_kind="current",
                     current_a=5, power_w=500, charge_voltage_limit_v=99,
                     discharge_voltage_limit_v=82)
    else:
        stage.update(mode="charge", current_a=2, voltage_v=99, power_w=300)
        if stage_type == "cccv":
            stage["cutoff_current_a"] = 0.2
    if stage_type != "cccv":
        stage["termination_conditions"] = [
            {"field": "voltage", "operator": ">=", "value": 99.0}
        ]
    data["stages"] = [stage, {"name": "skipped-test", "type": "rest", "duration_s": 0.2}]
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        assert final["state"] == "stopped", final
        assert final["stop_cause"]["origin"] == "duration_limit"
        assert report["outcome"] == "stopped"
        assert "error" not in report
        assert report["sequence_result"]["skipped_stages"] == ["skipped-test"]
        assert len(report["sequence_result"]["stages"]) == 2
        assert report["sequence_result"]["stages"][0]["termination_reason"] == "duration_limit"
        assert report["sequence_result"]["stages"][0]["termination_detail"]["condition_met"] is False
        assert report["sequence_result"]["stages"][-1]["state"] == "passed"
        assert report["final_safe_state_verified"] is True
        assert report["emergency_fallback_used"] is False
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


@pytest.mark.parametrize(
    ("setup_delay_s", "expected_state", "expected_reason"),
    [(0.0, "passed", "profile_end"), (0.7, "stopped", "duration_limit")],
)
def test_csv_end_and_duration_are_distinct_terminations(
    tmp_path, monkeypatch, setup_delay_s, expected_state, expected_reason
) -> None:
    profile = _profile(tmp_path / "workflow.json", post_sequence_rest_s=0.2)
    data = json.loads(profile.read_text(encoding="utf-8"))
    (tmp_path / "profile.csv").write_text(
        "time_s,current_a\n0,2\n0.2,1\n0.4,0\n", encoding="utf-8"
    )
    data["stages"] = [
        {
            "name": "csv-test", "type": "csv_profile", "duration_s": 0.6,
            "csv_path": "profile.csv", "profile_kind": "current",
            "current_a": 5, "power_w": 500,
            "voltage_limit_enabled": True, "current_limit_enabled": True,
            "power_limit_enabled": True, "charge_voltage_limit_v": 99,
            "discharge_voltage_limit_v": 82,
            "termination_conditions": [
                {"field": "voltage", "operator": ">=", "value": 99.0}
            ],
        },
        {"name": "next-rest", "type": "rest", "duration_s": 0.2},
    ]
    profile.write_text(json.dumps(data), encoding="utf-8")
    if setup_delay_s:
        original_setpoint = sequences_module.DynamicProfileStep._setpoint

        def delayed_first_point(self, value, *, mode=None):
            if value == 2.0:
                time.sleep(setup_delay_s)
            return original_setpoint(self, value, mode=mode)

        monkeypatch.setattr(
            sequences_module.DynamicProfileStep, "_setpoint", delayed_first_point
        )
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        assert final["state"] == expected_state, final
        assert report["sequence_result"]["stages"][0]["termination_reason"] == expected_reason
        if expected_state == "passed":
            assert [stage["termination_reason"] for stage in report["sequence_result"]["stages"]] == [
                "profile_end", "duration", "duration",
            ]
            assert report["sequence_result"]["skipped_stages"] == []
        else:
            assert final["stop_cause"]["origin"] == "duration_limit"
            assert report["sequence_result"]["skipped_stages"] == ["next-rest"]
            assert report["sequence_result"]["stages"][-1]["state"] == "passed"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


def test_discharge_sop_limits_power_then_duration_runs_final_rest(tmp_path) -> None:
    profile = _sop_profile(tmp_path / "workflow.json", duration_s=0.6)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["sop_control"]["min_discharge_w"] = 100.0
    data["stages"][0].update(
        mode="discharge", voltage_v=80,
        termination={"field": "voltage", "operator": "<=", "value": 80.5},
    )
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        client.submit_external_snapshot(
            "sim-remote", "bms-main", sequence=1,
            timestamp_utc=datetime.now(timezone.utc).isoformat(), health="ok",
            signals={"charge_sop_w": 300.0, "discharge_sop_w": 120.0},
        )
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        assert final["state"] == "stopped"
        assert final["stop_cause"]["origin"] == "duration_limit"
        assert report["sop_control"]["fault"] is None
        assert any(
            event["kind"] == "limit_applied" and event["applied_power_w"] == 120.0
            for event in report["sop_control"]["events"]
        )
        assert report["sequence_result"]["stages"][0]["termination_reason"] == "duration_limit"
        assert report["sequence_result"]["stages"][-1]["state"] == "passed"
        assert report["final_safe_state_verified"] is True
        assert report["emergency_fallback_used"] is False
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


@pytest.mark.parametrize(
    ("mode", "expected_w"), [("charge", 250.0), ("discharge", 120.0)]
)
def test_sop_signed_kw_is_normalized_before_power_limit(tmp_path, mode, expected_w) -> None:
    profile = _sop_profile(tmp_path / "workflow.json", duration_s=0.5)
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["sop_control"].update(
        unit="kW", charge_value_sign="negative", discharge_value_sign="positive"
    )
    data["stages"][0]["mode"] = mode
    data["stages"][0]["voltage_v"] = 100 if mode == "charge" else 80
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        client.submit_external_snapshot(
            "sim-remote", "bms-main", sequence=1,
            timestamp_utc=datetime.now(timezone.utc).isoformat(), health="ok",
            signals={"charge_sop_w": -0.25, "discharge_sop_w": 0.12},
        )
        run = client.start_workflow(
            "sim-remote", request_id=str(uuid.uuid4()),
            workflow_id="approved-rest-v1",
            bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
        )
        final = client.wait_workflow("sim-remote", run["run_id"], timeout_s=10,
                                     poll_interval_s=0.05)
        assert final["state"] == "passed", final
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        applied = [event for event in report["sop_control"]["events"]
                   if event["kind"] == "limit_applied"]
        assert applied[0]["applied_power_w"] == expected_w
        assert applied[0]["source"]["normalized_w"] == expected_w
        assert applied[0]["source"]["value"] == (-0.25 if mode == "charge" else 0.12)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


def test_sop_signed_kw_wrong_charge_sign_fails_before_start(tmp_path) -> None:
    profile = _sop_profile(tmp_path / "workflow.json")
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["sop_control"].update(unit="kW", charge_value_sign="negative")
    profile.write_text(json.dumps(data), encoding="utf-8")
    config = _config(tmp_path, profile)
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        client.submit_external_snapshot(
            "sim-remote", "bms-main", sequence=1,
            timestamp_utc=datetime.now(timezone.utc).isoformat(), health="ok",
            signals={"charge_sop_w": 0.25, "discharge_sop_w": 0.12},
        )
        with pytest.raises(NHRError, match="sop_zero_or_negative"):
            client.start_workflow(
                "sim-remote", request_id=str(uuid.uuid4()),
                workflow_id="approved-rest-v1",
                bundle_digest=config["workflow_registry"][0]["expected_bundle_digest"],
            )
        assert manager.get("sim-remote").instrument.connected is False
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()
