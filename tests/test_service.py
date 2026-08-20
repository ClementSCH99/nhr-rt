from __future__ import annotations

import threading
import time
from pathlib import Path
import logging
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import nhr9300.service as service_module
import pytest
from nhr9300.client import NHRServiceClient
from nhr9300.routines import Routine
from nhr9300.service import build_server
from nhr9300.types import OperatingState, Setpoints


def test_service_and_64_bit_compatible_client(tmp_path) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-1", "backend": "simulator"}],
        },
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        assert client.instruments() == [
            {"instrument_id": "sim-1", "connected": False}
        ]
        connected = client.connect("sim-1")
        assert connected["connected"] is True
        deadline = time.monotonic() + 1
        while client.acquisition("sim-1")["sample_count"] == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        acquisition = client.acquisition("sim-1")
        assert acquisition["requested_rate_hz"] == 5
        assert acquisition["active"] is True
        assert acquisition["sample_count"] >= 1
        assert acquisition["first_sample_at"] is not None
        assert acquisition["csv_path"].endswith(".csv")
        assert acquisition["last_error"] is None
        assert client.measurement("sim-1")["instrument_id"] == "sim-1"
        disabled = client.command("sim-1", "disable")
        assert disabled["enabled"] is False
        assert client.disconnect("sim-1") == {
            "detached": True,
            "service_connected": True,
        }
        assert client.status("sim-1")["connected"] is True
        assert client.acquisition("sim-1")["active"] is True
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_startup_summary_and_effective_configuration(tmp_path, capsys) -> None:
    config_path = Path(tmp_path) / "service.json"
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [
                {"id": "sim-start", "backend": "simulator", "rate_hz": 10}
            ],
        },
        port=0,
        config_path=config_path,
    )
    try:
        output = capsys.readouterr().out
        configuration = manager.configuration()
        instrument = configuration["instruments"][0]

        assert str(config_path) in output
        assert "sim-start backend=simulator rate=10 Hz" in output
        assert instrument["requested_rate_hz"] == 10
        assert instrument["csv_path"] in output
        assert configuration["listen_url"] in output
        assert configuration["restart_required_for_config_changes"] is True
    finally:
        manager.close()
        server.server_close()


def test_service_refuses_to_share_an_existing_port(tmp_path) -> None:
    config = {
        "output_dir": str(tmp_path),
        "instruments": [{"id": "sim-exclusive", "backend": "simulator"}],
    }
    server, manager = build_server(config, port=0, announce=False)
    host, port = server.server_address
    try:
        with pytest.raises(OSError):
            build_server(config, host=host, port=port, announce=False)
    finally:
        manager.close()
        server.server_close()


def test_stream_stop_event_closes_subscription_without_stopping_acquisition(
    tmp_path,
) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-stop", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    stop_event = threading.Event()
    samples = []

    def consume() -> None:
        for sample in client.stream("sim-stop", stop_event=stop_event):
            samples.append(sample)
            stop_event.set()

    consumer = threading.Thread(target=consume)
    try:
        client.connect("sim-stop")
        consumer.start()
        consumer.join(timeout=2)
        assert not consumer.is_alive()

        managed = manager.get("sim-stop")
        deadline = time.monotonic() + 1
        while managed.collector.subscriber_count:
            assert time.monotonic() < deadline
            time.sleep(0.01)

        assert samples
        assert managed.collector.running is True
        assert managed.collector.subscriber_count == 0
    finally:
        stop_event.set()
        consumer.join(timeout=2)
        client.disconnect("sim-stop")
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_closed_sse_client_is_logged_without_error_traceback(
    tmp_path, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="nhr9300.service")
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-close", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        client.connect("sim-close")
        managed = manager.get("sim-close")
        for _ in range(3):
            stream = client.stream("sim-close")
            next(stream)
            stream.close()
            deadline = time.monotonic() + 2
            while managed.collector.subscriber_count:
                assert time.monotonic() < deadline
                time.sleep(0.01)

        assert managed.collector.running is True
        assert managed.collector.subscriber_count == 0
        assert not [
            record
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]
    finally:
        client.disconnect("sim-close")
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_acquisition_failure_is_logged_as_error_and_exposed(tmp_path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="nhr9300.service")
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-error", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    server.RequestHandlerClass.stream_keepalive_interval_s = 0.05
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        managed = manager.get("sim-error")
        client.connect("sim-error")
        received = []
        consumer = threading.Thread(
            target=lambda: received.extend(client.stream("sim-error"))
        )
        consumer.start()
        deadline = time.monotonic() + 1
        while managed.collector.subscriber_count == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        managed.instrument._backend.failure = RuntimeError(  # type: ignore[attr-defined]
            "simulated acquisition failure"
        )
        consumer.join(timeout=2)
        assert not consumer.is_alive()
        state = client.acquisition("sim-error")

        assert state["last_error"]
        assert any(
            record.levelno == logging.ERROR
            and "acquisition stopped" in record.getMessage()
            for record in caplog.records
        )
    finally:
        backend = manager.get("sim-error").instrument._backend
        backend.failure = None  # type: ignore[attr-defined]
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_ctrl_c_stops_service_cleanly(monkeypatch, caplog) -> None:
    events = []

    class FakeServer:
        def serve_forever(self) -> None:
            events.append("serve")
            raise KeyboardInterrupt

        def server_close(self) -> None:
            events.append("server_close")

    class FakeManager:
        def close(self) -> None:
            events.append("manager_close")

    monkeypatch.setattr(
        service_module,
        "build_server",
        lambda *args, **kwargs: (FakeServer(), FakeManager()),
    )
    caplog.set_level(logging.INFO, logger="nhr9300.service")

    service_module.serve({})

    assert events == ["serve", "manager_close", "server_close"]
    messages = [record.getMessage() for record in caplog.records]
    assert "Service shutdown requested by operator (Ctrl+C)" in messages
    assert "NHR9300 service stopped" in messages


def test_versioned_and_legacy_read_routes_are_compatible(tmp_path) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-alias", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    root = f"http://{host}:{port}"
    versioned = NHRServiceClient(root)
    legacy = NHRServiceClient(root, api_version=None)
    try:
        assert versioned.instruments() == legacy.instruments()
        versioned.connect("sim-alias")
        assert versioned.status("sim-alias") == legacy.status("sim-alias")
        assert versioned.measurement("sim-alias")["instrument_id"] == "sim-alias"
        assert legacy.measurement("sim-alias")["instrument_id"] == "sim-alias"
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_multiple_observers_detach_without_stopping_runtime(tmp_path) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-shared", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    first = NHRServiceClient(f"http://{host}:{port}")
    second = NHRServiceClient(f"http://{host}:{port}")
    try:
        first.connect("sim-shared")
        first_stream = first.stream("sim-shared")
        second_stream = second.stream("sim-shared")
        assert next(first_stream)["instrument_id"] == "sim-shared"
        assert next(second_stream)["instrument_id"] == "sim-shared"
        first_stream.close()
        second_stream.close()

        assert first.disconnect("sim-shared")["detached"] is True
        managed = manager.get("sim-shared")
        assert managed.instrument.connected is True
        assert managed.collector.running is True
        assert second.measurement("sim-shared")["instrument_id"] == "sim-shared"
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_physical_primitive_control_is_forbidden_by_default(tmp_path) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [
                {
                    "id": "physical-policy-only",
                    "backend": "ivi",
                    "logical_name": "unused-policy-test",
                }
            ],
        },
        port=0,
        announce=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    request = Request(
        f"http://{host}:{port}/api/v1/instruments/physical-policy-only/limits",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with pytest.raises(HTTPError) as caught:
            urlopen(request, timeout=2.0)
        assert caught.value.code == 403
        assert "Primitive compatibility control is disabled" in (
            caught.value.read().decode("utf-8")
        )
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_service_owned_collector_survives_routine_completion(tmp_path) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-routine", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    try:
        managed = manager.get("sim-routine")
        managed.ensure_running()
        managed.runner.start(Routine("empty", []))
        assert managed.runner.wait(timeout=1.0)
        assert managed.collector.running is True
    finally:
        manager.close()
        server.server_close()


def test_manager_shutdown_disables_output_and_watchdog(tmp_path) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-shutdown", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    managed = manager.get("sim-shutdown")
    backend = managed.instrument._backend
    managed.ensure_running()
    backend.enabled = True
    backend.setpoints = Setpoints(state=OperatingState.CHARGE)
    managed.instrument.set_watchdog(True)
    assert backend.enabled is True
    assert backend.watchdog_enabled is True

    manager.close()

    assert backend.connected is False
    assert backend.enabled is False
    assert backend.watchdog_enabled is False
    server.server_close()
