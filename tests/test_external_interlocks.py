from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nhr9300.client import NHRServiceClient
from nhr9300.errors import NHRAPIError, NHRInterlockError, NHRValidationError
from nhr9300.execution import execute_workflow_on_runtime
from nhr9300.external_interlocks import (
    MAX_EXTERNAL_SOURCES,
    MAX_SIGNALS_PER_SNAPSHOT,
    ExternalInterlockManager,
    ExternalInterlockRule,
)
from nhr9300.service import MAX_JSON_BODY_BYTES, InstrumentManager, build_server
from nhr9300.workflow_registry import WorkflowBundle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rule(**overrides) -> ExternalInterlockRule:
    values = {
        "rule_id": "pack_voltage",
        "source_id": "bms-main",
        "signal": "pack_voltage_v",
        "comparison": "range",
        "unit": "V",
        "applies": "both",
        "max_age_s": 1.0,
        "minimum": 80.0,
        "maximum": 100.0,
    }
    values.update(overrides)
    return ExternalInterlockRule(**values)


def test_external_manager_accepts_numeric_and_boolean_rules() -> None:
    manager = ExternalInterlockManager()
    manager.submit(
        "bms-main",
        {
            "sequence": 1,
            "timestamp_utc": _now(),
            "health": "ok",
            "signals": {"pack_voltage_v": 90.0, "hv_permissive": True},
        },
    )
    manager.activate(
        [
            _rule(),
            _rule(
                rule_id="hv_permissive",
                signal="hv_permissive",
                comparison="equals",
                unit="boolean",
                minimum=None,
                maximum=None,
                expected=True,
            ),
        ]
    )

    manager.require_safe()
    assert [item.safe for item in manager.signals()] == [True, True]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"sequence": 1, "timestamp_utc": _now(), "health": "ok", "signals": {"v": float("nan")}}, "finite"),
        ({"sequence": True, "timestamp_utc": _now(), "health": "ok", "signals": {"v": 1}}, "sequence"),
        ({"sequence": 1, "timestamp_utc": "not-a-time", "health": "ok", "signals": {"v": 1}}, "ISO-8601"),
    ],
)
def test_invalid_snapshots_fail_closed(payload, message) -> None:
    manager = ExternalInterlockManager()
    manager.activate([_rule(signal="v")])

    with pytest.raises(NHRValidationError, match=message):
        manager.submit("bms-main", payload)
    with pytest.raises(NHRInterlockError, match="source_rejected"):
        manager.require_safe()


def test_missing_stale_unhealthy_and_out_of_order_fail_closed() -> None:
    manager = ExternalInterlockManager()
    manager.activate([_rule(max_age_s=0.05)])
    with pytest.raises(NHRInterlockError, match="source_missing"):
        manager.require_safe()

    manager.submit(
        "bms-main",
        {
            "sequence": 1,
            "timestamp_utc": _now(),
            "health": "fault",
            "signals": {"pack_voltage_v": 90},
        },
    )
    with pytest.raises(NHRInterlockError, match="source_health_fault"):
        manager.require_safe()

    manager.submit(
        "bms-main",
        {
            "sequence": 2,
            "timestamp_utc": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            "health": "ok",
            "signals": {"pack_voltage_v": 90},
        },
    )
    with pytest.raises(NHRInterlockError, match="signal_stale"):
        manager.require_safe()

    with pytest.raises(NHRValidationError, match="already accepted"):
        manager.submit(
            "bms-main",
            {
                "sequence": 2,
                "timestamp_utc": _now(),
                "health": "ok",
                "signals": {"pack_voltage_v": 90},
            },
        )
    with pytest.raises(NHRInterlockError, match="source_rejected"):
        manager.require_safe()


def test_identical_snapshot_retry_is_idempotent_but_conflict_fails_closed() -> None:
    manager = ExternalInterlockManager()
    manager.activate([_rule()])
    payload = {
        "sequence": 7,
        "timestamp_utc": _now(),
        "health": "ok",
        "signals": {"pack_voltage_v": 90, "hv_permissive": True},
    }

    accepted = manager.submit("bms-main", payload)
    retried = manager.submit("bms-main", payload)

    assert retried == accepted
    assert manager.status()["sources"][0]["last_rejection"] is None

    conflicting = dict(payload)
    conflicting["signals"] = {"pack_voltage_v": 91, "hv_permissive": True}
    with pytest.raises(NHRValidationError, match="different snapshot data"):
        manager.submit("bms-main", conflicting)
    with pytest.raises(NHRInterlockError, match="source_rejected"):
        manager.require_safe()
    assert manager.submit("bms-main", payload) == accepted
    with pytest.raises(NHRInterlockError, match="source_rejected"):
        manager.require_safe()


def test_runtime_violation_latches_but_prestart_recovers() -> None:
    manager = ExternalInterlockManager()
    manager.submit(
        "bms-main",
        {"sequence": 1, "timestamp_utc": _now(), "health": "ok", "signals": {"pack_voltage_v": 90}},
    )
    manager.activate([_rule()], phase="runtime", latch_runtime=True)
    manager.require_safe()
    manager.submit(
        "bms-main",
        {"sequence": 2, "timestamp_utc": _now(), "health": "ok", "signals": {"pack_voltage_v": 101}},
    )
    with pytest.raises(NHRInterlockError, match="condition_violated"):
        manager.require_safe()
    manager.submit(
        "bms-main",
        {"sequence": 3, "timestamp_utc": _now(), "health": "ok", "signals": {"pack_voltage_v": 90}},
    )
    with pytest.raises(NHRInterlockError, match="condition_violated"):
        manager.require_safe()
    status = manager.status()
    result = status["results"][0]
    assert result["value"] == 101.0
    assert result["source_sequence"] == 2
    assert result["source_timestamp_utc"] is not None
    assert result["source_received_at_utc"] is not None
    assert result["source_age_s_at_evaluation"] is not None
    assert status["sources"][0]["sequence"] == 3

    manager.activate([_rule()], phase="pre_start", latch_runtime=False)
    manager.require_safe()


def test_snapshot_source_and_signal_cardinality_are_bounded() -> None:
    manager = ExternalInterlockManager()
    with pytest.raises(NHRValidationError, match="signals cannot contain"):
        manager.submit(
            "too-many-signals",
            {
                "sequence": 1,
                "timestamp_utc": _now(),
                "health": "ok",
                "signals": {
                    f"signal_{index}": index
                    for index in range(MAX_SIGNALS_PER_SNAPSHOT + 1)
                },
            },
        )

    sources = ExternalInterlockManager()
    for index in range(MAX_EXTERNAL_SOURCES):
        sources.submit(
            f"source-{index}",
            {
                "sequence": 1,
                "timestamp_utc": _now(),
                "health": "ok",
                "signals": {"value": index},
            },
        )
    with pytest.raises(NHRValidationError, match="source limit"):
        sources.submit(
            "one-source-too-many",
            {
                "sequence": 1,
                "timestamp_utc": _now(),
                "health": "ok",
                "signals": {"value": 1},
            },
        )
    assert len(sources.status()["sources"]) == MAX_EXTERNAL_SOURCES


def test_safe_condition_must_remain_stable_before_start() -> None:
    manager = ExternalInterlockManager()
    manager.submit(
        "bms-main",
        {"sequence": 1, "timestamp_utc": _now(), "health": "ok", "signals": {"pack_voltage_v": 90}},
    )
    manager.activate([_rule(stability_duration_s=0.05)])
    with pytest.raises(NHRInterlockError, match="stabilizing"):
        manager.require_safe()
    # Leave margin for the coarse monotonic timer resolution of 32-bit Windows.
    time.sleep(0.12)
    manager.require_safe()


def test_controlled_stop_timeout_requests_emergency_fallback(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "workflow.json"
    _profile(profile)
    config = {
        "output_dir": str(tmp_path / "runs"),
        "instruments": [{"id": "sim-m5", "backend": "simulator"}],
        "workflow_registry": [],
    }
    _server, manager = build_server(config, port=0, announce=False)
    managed = manager.get("sim-m5")
    controller = managed.workflow_controller
    assert controller is not None

    class StillRunning:
        def join(self, _timeout):
            return None

        def is_alive(self):
            return True

    run_id = "fallback-test"
    controller._runs[run_id] = {  # focused white-box proof of the bounded fallback
        "run_id": run_id,
        "instrument_id": "sim-m5",
        "state": "stop_requested",
        "emergency_fallback_requested": False,
    }
    controller._threads[run_id] = StillRunning()
    calls = []
    monkeypatch.setattr(
        managed.instrument, "emergency_stop", lambda reason: calls.append(reason)
    )
    controller._enforce_stop_timeout(run_id, 0.01)

    assert controller._runs[run_id]["emergency_fallback_requested"] is True
    assert calls == ["Controlled workflow stop exceeded its approved timeout"]
    manager.close()
    _server.server_close()


def test_interlock_sse_payload_has_one_schema_for_all_publishers(tmp_path) -> None:
    config = {
        "output_dir": str(tmp_path / "runs"),
        "instruments": [{"id": "sim-m5", "backend": "simulator"}],
        "workflow_registry": [],
    }
    server, manager = build_server(config, port=0, announce=False)
    managed = manager.get("sim-m5")
    assert managed.event_broker is not None
    subscriber = managed.event_broker.subscribe()
    try:
        manager.submit_external_snapshot(
            "sim-m5",
            "bms-main",
            {
                "sequence": 1,
                "timestamp_utc": _now(),
                "health": "ok",
                "signals": {"pack_voltage_v": 90},
            },
        )
        submitted = subscriber.get(timeout=1)
        InstrumentManager._publish_runtime_updates(managed, None)
        periodic = subscriber.get(timeout=1)
        while periodic.event != "interlock":
            periodic = subscriber.get(timeout=1)

        expected_keys = {"instrument_id", "external_sources", "results"}
        assert submitted.event == "interlock"
        assert set(submitted.data) == expected_keys
        assert set(periodic.data) == expected_keys
    finally:
        managed.event_broker.unsubscribe(subscriber)
        manager.close()
        server.server_close()


def test_oversized_snapshot_body_is_rejected_before_parsing(tmp_path) -> None:
    config = {
        "output_dir": str(tmp_path / "runs"),
        "instruments": [{"id": "sim-m5", "backend": "simulator"}],
        "workflow_registry": [],
    }
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        with pytest.raises(NHRAPIError, match="JSON body exceeds"):
            client.submit_external_snapshot(
                "sim-m5",
                "bms-main",
                sequence=1,
                timestamp_utc=_now(),
                health="ok",
                signals={"oversized": "x" * MAX_JSON_BODY_BYTES},
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


def _profile(path) -> None:
    path.write_text(
        json.dumps(
            {
                "test_description": "M5 external interlock simulation",
                "bench_description": "simulated bench",
                "stop_procedure": "controlled stop then bounded emergency fallback",
                "expected_resource": "sim-m5",
                "expected_serial_number": "SIM-9300",
                "simulation_initial_voltage_v": 90,
                "watchdog_enabled": True,
                "safety_limits": {
                    "charge_current": 10, "charge_voltage_max": 100, "charge_power": 1000,
                    "discharge_current": 10, "discharge_voltage_min": 80, "discharge_power": 1000,
                    "approved": True, "profile_name": "approved-m5-sim-limits",
                },
                "workflow_limits": {
                    "max_current_a": 5, "max_power_w": 500,
                    "max_stage_duration_s": 10, "max_sequence_duration_s": 20,
                    "approved": True, "profile_name": "approved-m5-sim-workflow",
                },
                "external_interlocks": [
                    {
                        "rule_id": "pack_voltage", "source_id": "bms-main",
                        "signal": "pack_voltage_v", "comparison": "range", "unit": "V",
                        "applies": "both", "max_age_s": 2,
                        "minimum": 80, "maximum": 100,
                    },
                    {
                        "rule_id": "hv_permissive", "source_id": "bms-main",
                        "signal": "hv_permissive", "comparison": "equals",
                        "expected": True, "unit": "boolean", "applies": "both",
                        "max_age_s": 2,
                    },
                ],
                "stages": [
                    {
                        "name": "discharge", "type": "constant_current", "duration_s": 3,
                        "mode": "discharge", "current_a": 1, "voltage_v": 80,
                        "power_w": 500, "voltage_limit_enabled": True,
                        "current_limit_enabled": True, "power_limit_enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_unrelated_error_is_not_masked_by_a_pending_stop(
    tmp_path, monkeypatch
) -> None:
    profile = tmp_path / "workflow.json"
    _profile(profile)
    config = {
        "output_dir": str(tmp_path / "runs"),
        "instruments": [{"id": "sim-m5", "backend": "simulator"}],
        "workflow_registry": [],
    }
    server, manager = build_server(config, port=0, announce=False)
    managed = manager.get("sim-m5")
    stop_event = threading.Event()
    stop_event.set()

    def fail_sequence(*_args, **_kwargs):
        raise RuntimeError("independent backend fault")

    monkeypatch.setattr("nhr9300.execution.SequenceRunner.run", fail_sequence)
    try:
        outcome = execute_workflow_on_runtime(
            profile=profile,
            output=tmp_path / "classification",
            run_id="classification-test",
            instrument=managed.instrument,
            collector=managed.collector,
            hardware=False,
            stop_event=stop_event,
            external_interlock_evidence=managed.external_interlocks.status,
        )
        report = json.loads(outcome.report_path.read_text(encoding="utf-8"))
        assert report["outcome"] == "failed"
        assert "independent backend fault" in report["error"]
        assert report.get("stopped") is not True
    finally:
        manager.close()
        server.server_close()


def test_service_blocks_start_then_controlled_stops_and_preserves_evidence(tmp_path) -> None:
    profile = tmp_path / "workflow.json"
    _profile(profile)
    bundle = WorkflowBundle.load(profile, hardware=False)
    config = {
        "output_dir": str(tmp_path / "runs"),
        "instruments": [
            {"id": "sim-m5", "backend": "simulator", "rate_hz": 10, "remote_workflow_control": True}
        ],
        "workflow_registry": [
            {
                "workflow_id": "m5-v1", "instrument_id": "sim-m5",
                "profile_path": str(profile), "expected_resource": "sim-m5",
                "expected_serial_number": "SIM-9300", "expected_bundle_digest": bundle.digest,
                "approved": True,
            }
        ],
    }
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        with pytest.raises(NHRAPIError, match="source_missing"):
            client.start_workflow(
                "sim-m5",
                request_id=str(uuid.uuid4()),
                workflow_id="m5-v1",
                bundle_digest=bundle.digest,
            )

        first_timestamp = _now()
        accepted = client.submit_external_snapshot(
            "sim-m5", "bms-main", sequence=1, timestamp_utc=first_timestamp,
            health="ok", signals={"pack_voltage_v": 90, "hv_permissive": False},
        )
        assert accepted["sequence"] == 1
        assert client.submit_external_snapshot(
            "sim-m5", "bms-main", sequence=1, timestamp_utc=first_timestamp,
            health="ok", signals={"pack_voltage_v": 90, "hv_permissive": False},
        ) == accepted
        with pytest.raises(NHRAPIError, match="hv_permissive"):
            client.start_workflow(
                "sim-m5",
                request_id=str(uuid.uuid4()),
                workflow_id="m5-v1",
                bundle_digest=bundle.digest,
            )

        client.submit_external_snapshot(
            "sim-m5", "bms-main", sequence=2, timestamp_utc=_now(),
            health="ok", signals={"pack_voltage_v": 90, "hv_permissive": True},
        )
        stale_run = client.start_workflow(
            "sim-m5",
            request_id=str(uuid.uuid4()),
            workflow_id="m5-v1",
            bundle_digest=bundle.digest,
        )
        stale_final = client.wait_workflow(
            "sim-m5", stale_run["run_id"], timeout_s=5, poll_interval_s=0.05
        )
        assert stale_final["state"] == "stopped", json.dumps(stale_final)
        stale_results = stale_final["stop_cause"]["detail"]["results"]
        assert any(item["reason"] == "signal_stale" for item in stale_results)

        client.submit_external_snapshot(
            "sim-m5", "bms-main", sequence=3, timestamp_utc=_now(),
            health="ok", signals={"pack_voltage_v": 90, "hv_permissive": True},
        )
        started = client.start_workflow(
            "sim-m5",
            request_id=str(uuid.uuid4()),
            workflow_id="m5-v1",
            bundle_digest=bundle.digest,
        )
        deadline = time.monotonic() + 3
        while not client.runtime("sim-m5")["instrument"]["output_enabled"]:
            assert time.monotonic() < deadline
            time.sleep(0.02)

        client.submit_external_snapshot(
            "sim-m5", "bms-main", sequence=4, timestamp_utc=_now(),
            health="ok", signals={"pack_voltage_v": 101, "hv_permissive": True},
        )
        final = client.wait_workflow("sim-m5", started["run_id"], timeout_s=5)
        assert final["state"] == "stopped"
        assert final["stop_cause"]["origin"] == "external_interlock"
        assert final["final_safe_state"]["verified"] is True
        report = json.loads(Path(final["report_path"]).read_text(encoding="utf-8"))
        evidence = report["external_interlocks"]
        assert evidence["latched"] is True
        assert evidence["stop_cause"]["origin"] == "external_interlock"
        violated = next(
            item
            for item in evidence["results"]
            if item["reason"] == "condition_violated"
        )
        assert violated["source_sequence"] == 4
        assert violated["source_timestamp_utc"] is not None
        assert violated["source_received_at_utc"] is not None
        assert violated["source_age_s_at_evaluation"] is not None
        cause_violation = next(
            item
            for item in final["stop_cause"]["detail"]["results"]
            if item["reason"] == "condition_violated"
        )
        assert cause_violation["source_sequence"] == 4
        assert client.interlocks("sim-m5")["instrument_id"] == "sim-m5"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()
