from __future__ import annotations

import threading
import time

from nhr9300.client import NHRServiceClient
from nhr9300.observability import (
    EVENT_TYPES,
    RuntimeEventBroker,
    RuntimeEventPublisher,
)
from nhr9300.service import build_server


def _server(tmp_path, **config_overrides):
    config = {
        "output_dir": str(tmp_path),
        "instruments": [{"id": "sim-runtime", "backend": "simulator"}],
        **config_overrides,
    }
    server, manager = build_server(config, port=0, announce=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, manager, thread, NHRServiceClient(f"http://{host}:{port}")


def _close(server, manager, thread) -> None:
    server.shutdown()
    thread.join(timeout=2)
    manager.close()
    server.server_close()


def test_runtime_snapshot_is_read_only_and_explicit_before_connection(tmp_path) -> None:
    server, manager, thread, client = _server(tmp_path)
    try:
        snapshot = client.runtime("sim-runtime")

        assert snapshot["schema_version"] == "1.0"
        assert snapshot["instrument"] == {
            "instrument_id": "sim-runtime",
            "connected": False,
            "remote": False,
            "state": 0,
            "state_name": "OFF",
            "output_enabled": False,
            "setpoints": None,
        }
        assert snapshot["measurement"]["available"] is False
        assert snapshot["workflow"]["state"] == "idle"
        assert snapshot["workflow"]["progress_available"] is False
        assert snapshot["external_sources"]["status"] == "not_configured"
        assert snapshot["effective_power_limits"]["configured"] is False
        assert manager.get("sim-runtime").instrument.connected is False
    finally:
        _close(server, manager, thread)


def test_runtime_snapshot_consolidates_live_measurement_and_evidence(tmp_path) -> None:
    server, manager, thread, client = _server(tmp_path)
    try:
        client.connect("sim-runtime")
        deadline = time.monotonic() + 2
        while not client.runtime("sim-runtime")["measurement"]["available"]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        snapshot = client.runtime("sim-runtime")

        assert snapshot["instrument"]["connected"] is True
        assert snapshot["measurement"]["fresh"] is True
        assert snapshot["measurement"]["value"]["instrument_id"] == "sim-runtime"
        assert snapshot["acquisition"]["health"] == "ok"
        assert snapshot["acquisition"]["evidence_path"].endswith(".csv")
        assert snapshot["interlocks"]["results"][0]["safe"] is True
        assert set(snapshot["totals"]) == {
            "capacity_charge_ah",
            "capacity_discharge_ah",
            "energy_charge_wh",
            "energy_discharge_wh",
        }
    finally:
        _close(server, manager, thread)


def test_runtime_snapshot_does_not_wait_for_workflow_lifecycle_lock(tmp_path) -> None:
    server, manager, thread, client = _server(tmp_path)
    managed = manager.get("sim-runtime")
    lock_held = threading.Event()
    release_lock = threading.Event()
    request_done = threading.Event()
    result = []

    def hold_lifecycle_lock() -> None:
        with managed._lifecycle_lock:
            lock_held.set()
            release_lock.wait(2)

    def request_runtime() -> None:
        try:
            result.append(client.runtime("sim-runtime"))
        finally:
            request_done.set()

    holder = threading.Thread(target=hold_lifecycle_lock)
    requester = threading.Thread(target=request_runtime)
    try:
        holder.start()
        assert lock_held.wait(1)
        requester.start()
        assert request_done.wait(0.5)
        assert result[0]["schema_version"] == "1.0"
    finally:
        release_lock.set()
        holder.join(timeout=2)
        requester.join(timeout=2)
        _close(server, manager, thread)


def test_acquisition_failure_is_an_active_runtime_alert(tmp_path) -> None:
    server, manager, thread, client = _server(tmp_path)
    managed = manager.get("sim-runtime")
    backend = managed.instrument._backend
    try:
        client.connect("sim-runtime")
        deadline = time.monotonic() + 2
        while managed.collector.latest is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        backend.failure = RuntimeError("observed acquisition failure")
        while managed.collector.running:
            assert time.monotonic() < deadline
            time.sleep(0.01)

        snapshot = client.runtime("sim-runtime")
        assert snapshot["acquisition"]["health"] == "error"
        assert snapshot["alerts"][0]["code"] == "acquisition_error"
        assert "observed acquisition failure" in snapshot["alerts"][0]["message"]
    finally:
        backend.failure = None
        _close(server, manager, thread)


def test_runtime_events_allow_independent_viewers_and_clean_disconnect(tmp_path) -> None:
    server, manager, thread, client = _server(tmp_path)
    first = client.events("sim-runtime")
    second = client.events("sim-runtime")
    try:
        first_event = next(first)
        second_event = next(second)

        assert first_event["event"] in EVENT_TYPES
        assert second_event["event"] in EVENT_TYPES
        assert first_event["schema_version"] == "1.0"
        first.close()
        second.close()
        broker = manager.get("sim-runtime").event_broker
        assert broker is not None
        deadline = time.monotonic() + 2
        while broker.subscriber_count:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert broker.subscriber_count == 0
        assert manager.get("sim-runtime").instrument.connected is False
    finally:
        first.close()
        second.close()
        _close(server, manager, thread)


def test_managed_runtime_observer_owns_thread_and_can_refresh(tmp_path) -> None:
    server, manager, thread, client = _server(tmp_path)
    try:
        with client.observe_events("sim-runtime") as observer:
            event = observer.get(timeout_s=2)
            assert event["event"] in EVENT_TYPES
            assert observer.last_sequence == event["sequence"]
            observer.needs_runtime_refresh = True
            assert observer.refresh_runtime()["schema_version"] == "1.0"
            assert observer.needs_runtime_refresh is False
        assert observer._thread is not None
        assert not observer._thread.is_alive()
    finally:
        _close(server, manager, thread)


def test_slow_runtime_subscriber_drops_intermediate_events_without_blocking() -> None:
    broker = RuntimeEventBroker("sim", subscriber_queue_size=2)
    subscriber = broker.subscribe()

    for value in range(10):
        broker.publish("measurement", {"value": value})

    assert subscriber.qsize() == 2
    assert subscriber.get_nowait().data["value"] == 8
    assert subscriber.get_nowait().data["value"] == 9
    assert broker.take_dropped_count(subscriber) == 8


def test_runtime_event_broker_shutdown_is_bounded() -> None:
    broker = RuntimeEventBroker("sim")
    broker.subscribe()
    started = time.monotonic()

    broker.close()

    assert broker.closed is True
    assert time.monotonic() - started < 0.1


def test_service_shutdown_ends_runtime_event_stream_with_bounded_eof(tmp_path) -> None:
    server, manager, thread, client = _server(tmp_path)
    stream = client.events("sim-runtime")
    try:
        next(stream)
        started = time.monotonic()
        manager.close()

        while True:
            try:
                next(stream)
            except StopIteration:
                break
            assert time.monotonic() - started < 1.0
        assert time.monotonic() - started < 1.0
    finally:
        stream.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_publication_error_is_isolated_from_the_producer() -> None:
    first_attempt = threading.Event()
    recovered = threading.Event()
    calls = 0

    def callback(value) -> None:
        nonlocal calls
        calls += 1
        if value == "bad":
            first_attempt.set()
            raise RuntimeError("viewer serialization failed")
        recovered.set()

    publisher = RuntimeEventPublisher(callback)
    try:
        publisher.submit("bad")
        assert first_attempt.wait(1)
        publisher.submit("good")
        assert recovered.wait(1)
        assert calls == 2
    finally:
        publisher.close()
