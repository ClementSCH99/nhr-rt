from __future__ import annotations

import threading
import time
from pathlib import Path
import logging

import nhr9300.service as service_module
import pytest
from nhr9300.client import NHRServiceClient
from nhr9300.service import build_server


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
        assert client.disconnect("sim-1") == {"connected": False}
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
        assert list(client.stream("sim-error")) == []
        state = client.acquisition("sim-error")

        assert state["last_error"]
        assert any(
            record.levelno == logging.ERROR
            and "acquisition stopped" in record.getMessage()
            for record in caplog.records
        )
    finally:
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
