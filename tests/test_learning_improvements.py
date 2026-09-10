from __future__ import annotations

import json
from pathlib import Path

import pytest

from nhr9300.client import ExternalSnapshotPublisher, NHRServiceClient
from nhr9300.diagnostics import diagnose_config
from nhr9300.errors import (
    NHRAPIError,
    NHREvidencePersistenceError,
    NHRPendingSnapshotError,
    NHRProtocolError,
    NHRTransportError,
    NHRWorkflowTimeout,
)
from nhr9300.evidence import atomic_write_json


def test_atomic_json_write_retries_permission_error_and_uses_unique_temp(tmp_path) -> None:
    target = tmp_path / "run-state.json"
    sources: list[Path] = []
    calls = 0

    def flaky_replace(source, destination) -> None:
        nonlocal calls
        calls += 1
        sources.append(Path(source))
        if calls < 3:
            raise PermissionError("simulated sharing violation")
        Path(source).replace(destination)

    atomic_write_json(target, {"state": "running"}, replace=flaky_replace, backoff_s=0)

    assert json.loads(target.read_text(encoding="utf-8")) == {"state": "running"}
    assert calls == 3
    assert all(source.name != "run-state.json.tmp" for source in sources)
    assert not sources[0].exists()


def test_atomic_json_write_reports_persistent_failure(tmp_path) -> None:
    def denied(source, destination) -> None:
        raise PermissionError("locked")

    with pytest.raises(NHREvidencePersistenceError, match="non-synchronized"):
        atomic_write_json(
            tmp_path / "run-state.json",
            {"state": "running"},
            attempts=2,
            backoff_s=0,
            replace=denied,
        )


def test_wait_workflow_returns_terminal_and_timeout_does_not_stop(monkeypatch) -> None:
    client = NHRServiceClient()
    states = iter(({"state": "running"}, {"state": "passed", "run_id": "r1"}))
    monkeypatch.setattr(client, "workflow_run", lambda *_: next(states))
    assert client.wait_workflow("sim", "r1", timeout_s=1, poll_interval_s=0.001)["state"] == "passed"

    monkeypatch.setattr(client, "workflow_run", lambda *_: {"state": "running"})
    stop_calls: list[object] = []
    monkeypatch.setattr(client, "stop_workflow", lambda *_: stop_calls.append(object()))
    with pytest.raises(NHRWorkflowTimeout, match="no stop request was sent"):
        client.wait_workflow("sim", "r2", timeout_s=0.001, poll_interval_s=0.001)
    assert stop_calls == []


def test_invalid_json_is_a_protocol_error(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr("nhr9300.client.urlopen", lambda request, timeout: Response())
    with pytest.raises(NHRProtocolError):
        NHRServiceClient().configuration()


def test_transport_error_warns_that_state_is_unknown(monkeypatch) -> None:
    def unavailable(request, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr("nhr9300.client.urlopen", unavailable)
    with pytest.raises(NHRTransportError, match="state are unknown"):
        NHRServiceClient().configuration()


def test_external_snapshot_uses_caller_timeout(monkeypatch) -> None:
    observed: dict[str, float] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self) -> bytes:
            return b'{"sequence": 1}'

    def respond(request, timeout):
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr("nhr9300.client.urlopen", respond)
    NHRServiceClient().submit_external_snapshot(
        "sim",
        "bms-main",
        sequence=1,
        timestamp_utc="2026-09-10T12:00:00+00:00",
        health="ok",
        signals={"pack_voltage_v": 90.0},
        timeout_s=0.75,
    )

    assert observed["timeout"] == 0.75


def test_external_snapshot_publisher_preserves_ambiguous_retry(monkeypatch) -> None:
    client = NHRServiceClient()
    monkeypatch.setattr(
        client,
        "interlocks",
        lambda _instrument_id: {
            "external_sources": {
                "sources": [{"source_id": "bms-main", "sequence": 4}]
            }
        },
    )
    submissions: list[dict] = []

    def submit(_instrument_id, source_id, **payload):
        submissions.append({"source_id": source_id, **payload})
        if len(submissions) == 1:
            raise NHRTransportError("response lost")
        return {"source_id": source_id, "sequence": payload["sequence"]}

    monkeypatch.setattr(client, "submit_external_snapshot", submit)
    publisher = ExternalSnapshotPublisher(
        client, "sim", "bms-main", timeout_s=0.5
    )

    with pytest.raises(NHRTransportError, match="response lost"):
        publisher.publish(
            timestamp_utc="2026-09-10T12:00:00+00:00",
            health="ok",
            signals={"pack_voltage_v": 90.0},
        )
    assert publisher.pending_sequence == 5
    with pytest.raises(NHRPendingSnapshotError, match="Retry the pending"):
        publisher.publish(
            timestamp_utc="2026-09-10T12:00:01+00:00",
            health="ok",
            signals={"pack_voltage_v": 91.0},
        )

    receipt = publisher.retry_pending()

    assert receipt["sequence"] == 5
    assert submissions[0] == submissions[1]
    assert publisher.pending_sequence is None
    assert publisher.next_sequence == 6


def test_external_snapshot_publisher_resynchronizes_after_api_rejection(
    monkeypatch,
) -> None:
    client = NHRServiceClient()
    monkeypatch.setattr(
        client,
        "interlocks",
        lambda _instrument_id: {
            "external_sources": {
                "sources": [{"source_id": "bms-main", "sequence": 8}]
            }
        },
    )
    attempts = 0

    def submit(_instrument_id, source_id, **payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise NHRAPIError(
                "rejected", status=400, code="invalid_request"
            )
        return {"source_id": source_id, "sequence": payload["sequence"]}

    monkeypatch.setattr(client, "submit_external_snapshot", submit)
    publisher = ExternalSnapshotPublisher(client, "sim", "bms-main")

    with pytest.raises(NHRAPIError, match="rejected"):
        publisher.publish(
            timestamp_utc="2026-09-10T12:00:00+00:00",
            health="ok",
            signals={"pack_voltage_v": 90.0},
        )
    assert publisher.pending_sequence is None
    assert publisher.next_sequence is None

    receipt = publisher.publish(
        timestamp_utc="2026-09-10T12:00:01+00:00",
        health="ok",
        signals={"pack_voltage_v": 91.0},
    )
    assert receipt["sequence"] == 9


def test_doctor_checks_output_and_registry_without_connecting(tmp_path) -> None:
    profile = tmp_path / "workflow.json"
    profile.write_text(
        json.dumps(
            {
                "test_description": "doctor",
                "bench_description": "simulation",
                "stop_procedure": "request stop",
                "expected_resource": "sim",
                "expected_serial_number": "SIM-9300",
                "simulation_initial_voltage_v": 90,
                "watchdog_enabled": False,
                "safety_limits": {
                    "charge_current": 2,
                    "charge_voltage_max": 100,
                    "charge_power": 200,
                    "discharge_current": 2,
                    "discharge_voltage_min": 80,
                    "discharge_power": 200,
                    "approved": True,
                    "profile_name": "doctor-limits",
                },
                "workflow_limits": {
                    "max_current_a": 1,
                    "max_power_w": 100,
                    "max_stage_duration_s": 2,
                    "max_sequence_duration_s": 2,
                    "approved": True,
                    "profile_name": "doctor-workflow",
                },
                "stages": [{"name": "rest", "type": "rest", "duration_s": 0.1}],
            }
        ),
        encoding="utf-8",
    )
    from nhr9300.workflow_registry import WorkflowBundle

    digest = WorkflowBundle.load(profile, hardware=False).digest
    config = tmp_path / "service.json"
    config.write_text(
        json.dumps(
            {
                "output_dir": str(tmp_path / "evidence"),
                "instruments": [{"id": "sim", "backend": "simulator"}],
                "workflow_registry": [
                    {
                        "workflow_id": "doctor",
                        "instrument_id": "sim",
                        "profile_path": "workflow.json",
                        "expected_resource": "sim",
                        "expected_serial_number": "SIM-9300",
                        "expected_bundle_digest": digest,
                        "approved": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = diagnose_config(config)

    assert result["safe_scope"] == "software_and_read_only_no_instrument_connection"
    assert result["output_dir"]["atomic_replace"] is True
    assert result["workflows"][0]["available"] is True
    assert not list((tmp_path / "evidence").glob(".nhr9300-diagnostic-*"))
