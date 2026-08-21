from __future__ import annotations

import json
import threading

from http.client import HTTPConnection
from urllib.request import urlopen

import pytest

from nhr9300.client import NHRServiceClient
from nhr9300.monitor import build_monitor, require_local_service_url
from nhr9300.service import build_server


class RecordingRuntimeReader:
    def __init__(self, snapshot=None, error: Exception | None = None) -> None:
        self.snapshot = snapshot or {"schema_version": "1.0"}
        self.error = error
        self.calls: list[str] = []

    def runtime(self, instrument_id: str):
        self.calls.append(instrument_id)
        if self.error is not None:
            raise self.error
        return self.snapshot


def _start_monitor(reader, **overrides):
    server = build_monitor(reader, "sim-monitor", port=0, **overrides)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, thread, f"http://{host}:{port}"


def _close_server(server, thread) -> None:
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


def test_monitor_serves_local_assets_and_read_only_configuration() -> None:
    reader = RecordingRuntimeReader()
    server, thread, url = _start_monitor(reader)
    try:
        with urlopen(url + "/") as response:
            html = response.read().decode("utf-8")
            assert response.headers["Content-Security-Policy"]
        assert "Runtime monitor" in html
        assert "READ ONLY" in html
        assert "<button" not in html
        assert "<form" not in html

        with urlopen(url + "/assets/styles.css") as response:
            assert response.headers.get_content_type() == "text/css"
            assert b"--cyan" in response.read()
        with urlopen(url + "/assets/app.js") as response:
            script = response.read().decode("utf-8")
            assert 'fetch("/api/runtime"' in script
            assert 'value == null || value === ""' in script
            assert '1: "STANDBY"' in script
            assert 'UNKNOWN · SERVICE OFFLINE' in script
            assert "/connect" not in script
            assert "/disconnect" not in script
            assert "/workflow" not in script

        with urlopen(url + "/api/config") as response:
            config = json.load(response)
        assert config == {
            "instrument_id": "sim-monitor",
            "refresh_interval_s": 1.0,
            "trend_points": 600,
            "read_only": True,
        }
        assert reader.calls == []
    finally:
        _close_server(server, thread)


def test_runtime_route_transparently_uses_only_runtime_reader() -> None:
    expected = {
        "schema_version": "1.0",
        "instrument": {"instrument_id": "sim-monitor", "connected": False},
        "alerts": [{"code": "fixture_alert", "message": "fixture"}],
    }
    reader = RecordingRuntimeReader(expected)
    server, thread, url = _start_monitor(reader)
    try:
        with urlopen(url + "/api/runtime") as response:
            assert json.load(response) == expected
        assert reader.calls == ["sim-monitor"]
    finally:
        _close_server(server, thread)


def test_monitor_rejects_every_write_method() -> None:
    reader = RecordingRuntimeReader()
    server, thread, url = _start_monitor(reader)
    host, port = server.server_address[:2]
    try:
        for method in ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"]:
            connection = HTTPConnection(host, port, timeout=2)
            connection.request(method, "/api/runtime", body=b"{}")
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 405
            assert payload["error"] == "read_only_monitor"
            assert reader.calls == []
            connection.close()
    finally:
        _close_server(server, thread)


def test_service_failure_is_visible_without_a_control_fallback() -> None:
    reader = RecordingRuntimeReader(error=ConnectionError("service is offline"))
    server, thread, url = _start_monitor(reader)
    try:
        connection = HTTPConnection(*server.server_address[:2], timeout=2)
        connection.request("GET", "/api/runtime")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 502
        assert payload == {
            "error": "service_unavailable",
            "message": "service is offline",
            "instrument_id": "sim-monitor",
        }
        assert reader.calls == ["sim-monitor"]
        connection.close()
    finally:
        _close_server(server, thread)


def test_open_refresh_and_close_do_not_change_simulator_state(tmp_path) -> None:
    service, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-monitor", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    service_thread = threading.Thread(target=service.serve_forever, daemon=True)
    service_thread.start()
    service_host, service_port = service.server_address[:2]
    client = NHRServiceClient(f"http://{service_host}:{service_port}")
    monitor, monitor_thread, monitor_url = _start_monitor(client)
    try:
        before = client.runtime("sim-monitor")
        for _ in range(3):
            with urlopen(monitor_url + "/") as response:
                assert response.status == 200
            with urlopen(monitor_url + "/api/runtime") as response:
                observed = json.load(response)
                assert observed["instrument"] == before["instrument"]
        _close_server(monitor, monitor_thread)
        after = client.runtime("sim-monitor")
        assert before["instrument"] == after["instrument"]
        assert before["measurement"]["available"] is False
        assert after["measurement"]["available"] is False
        assert manager.get("sim-monitor").instrument.connected is False
    finally:
        if monitor_thread.is_alive():
            _close_server(monitor, monitor_thread)
        service.shutdown()
        service_thread.join(timeout=2)
        manager.close()
        service.server_close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"host": "0.0.0.0"}, "localhost only"),
        ({"instrument_id": ""}, "must not be empty"),
        ({"refresh_interval_s": 0}, "must be positive"),
        ({"trend_points": 1}, "greater than one"),
    ],
)
def test_monitor_configuration_is_bounded(kwargs, message: str) -> None:
    values = {"runtime_reader": RecordingRuntimeReader(), "instrument_id": "sim"}
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        build_monitor(**values)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:9300",
        "http://192.0.2.1:9300",
        "http://user:secret@127.0.0.1:9300",
    ],
)
def test_monitor_rejects_nonlocal_or_authenticated_service_urls(url: str) -> None:
    with pytest.raises(ValueError, match="localhost HTTP URL"):
        require_local_service_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:9300", "http://localhost:9300", "http://[::1]:9300"],
)
def test_monitor_accepts_local_service_urls(url: str) -> None:
    require_local_service_url(url)
