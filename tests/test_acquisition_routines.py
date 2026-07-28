from __future__ import annotations

import csv
import threading
import time

from nhr9300 import (
    NHR9300,
    OperatingState,
    SafetyLimits,
    SimulatedBackend,
    StaticInterlockProvider,
)
from nhr9300.acquisition import AcquisitionCollector
from nhr9300.routines import RoutineRunner, constant_current_hold, load_yaml
from nhr9300.types import RoutineState


def limits() -> SafetyLimits:
    return SafetyLimits(
        charge_current=5,
        charge_voltage_max=420,
        charge_power=2000,
        discharge_current=5,
        discharge_voltage_min=280,
        discharge_power=2000,
        approved=True,
        profile_name="simulated",
    )


def setup(tmp_path, name="sim"):
    instrument = NHR9300(
        name,
        SimulatedBackend(name),
        interlocks=[StaticInterlockProvider()],
    )
    instrument.connect()
    collector = AcquisitionCollector(
        instrument, rate_hz=10, csv_path=tmp_path / f"{name}.csv"
    )
    return instrument, collector


def routine(name: str = "short"):
    return constant_current_hold(
        name=name,
        limits=limits(),
        mode=OperatingState.DISCHARGE,
        current_a=1,
        voltage_v=300,
        power_w=500,
        duration_s=0.3,
        arm_duration_s=5,
    )


def test_routine_passes_and_returns_safe_state(tmp_path) -> None:
    instrument, collector = setup(tmp_path)
    result = RoutineRunner(instrument, collector).run(routine())
    status = instrument.read_status()
    instrument.close()
    assert result.state == RoutineState.PASSED
    assert status.state == OperatingState.STANDBY
    assert status.enabled is False
    with collector.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {
        "voltage_v",
        "current_a",
        "power_w",
        "routine_id",
        "interlocks",
    }.issubset(rows[0])


def test_two_independent_routines_run_concurrently(tmp_path) -> None:
    first, first_collector = setup(tmp_path, "sim-1")
    second, second_collector = setup(tmp_path, "sim-2")
    results = []

    def execute(instrument, collector):
        results.append(RoutineRunner(instrument, collector).run(routine()))

    threads = [
        threading.Thread(target=execute, args=(first, first_collector)),
        threading.Thread(target=execute, args=(second, second_collector)),
    ]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started
    first.close()
    second.close()
    assert elapsed < 0.6
    assert all(result.state == RoutineState.PASSED for result in results)


def test_example_yaml_is_safe_by_default() -> None:
    loaded = load_yaml("examples/cc_hold.example.yaml")
    assert loaded.name
    assert loaded.steps[0].limits.approved is False


def test_acquisition_reports_csv_and_timing_quality(tmp_path) -> None:
    instrument, collector = setup(tmp_path, "timing")
    collector.start()
    time.sleep(0.35)
    collector.stop()
    statistics = collector.statistics()
    instrument.close()

    with collector.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert statistics.error is None
    assert statistics.sample_count == len(rows)
    assert statistics.sample_count >= 3
    assert statistics.effective_rate_hz > 0
    assert statistics.mean_interval_s is not None
    assert statistics.max_interval_s is not None


def test_simulated_acquisition_supports_1_5_and_10_hz(tmp_path) -> None:
    for rate_hz in (1, 5, 10):
        instrument = NHR9300(
            f"rate-{rate_hz}",
            SimulatedBackend(f"rate-{rate_hz}"),
            interlocks=[StaticInterlockProvider()],
        )
        instrument.connect()
        collector = AcquisitionCollector(
            instrument,
            rate_hz=rate_hz,
            csv_path=tmp_path / f"rate-{rate_hz}.csv",
        )
        collector.start()
        time.sleep((2.2 / rate_hz) + 0.05)
        collector.stop()
        state = collector.state()
        instrument.close()

        assert state.requested_rate_hz == rate_hz
        assert state.sample_count >= 2
        assert state.first_sample_at is not None
        assert state.observed_rate_hz > 0
        assert state.last_error is None


def test_each_acquisition_uses_a_unique_timestamped_csv(tmp_path) -> None:
    instrument, collector = setup(tmp_path, "unique")
    collector.start()
    time.sleep(0.05)
    collector.stop()
    first_path = collector.csv_path

    collector.start()
    time.sleep(0.05)
    collector.stop()
    second_path = collector.csv_path
    instrument.close()

    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()
    assert first_path.name.startswith("unique_")
    assert second_path.name.startswith("unique_")


def test_subscribers_can_be_removed_without_affecting_acquisition(tmp_path) -> None:
    instrument, collector = setup(tmp_path, "subscribers")
    first = collector.subscribe()
    second = collector.subscribe()
    assert collector.subscriber_count == 2

    collector.unsubscribe(first)
    collector.unsubscribe(first)
    assert collector.subscriber_count == 1

    collector.start()
    sample = second.get(timeout=1)
    collector.unsubscribe(second)
    collector.stop()
    instrument.close()

    assert sample.measurement.instrument_id == "subscribers"
    assert collector.subscriber_count == 0


def test_acquisition_statistics_reset_on_restart(tmp_path) -> None:
    instrument, collector = setup(tmp_path, "restart")
    collector.start()
    time.sleep(0.25)
    collector.stop()
    first_count = collector.statistics().sample_count

    collector.start()
    time.sleep(0.15)
    collector.stop()
    second_count = collector.statistics().sample_count
    instrument.close()

    assert first_count >= 2
    assert 1 <= second_count < first_count + second_count


def test_status_is_cached_but_refreshed_on_step_change(tmp_path) -> None:
    backend = SimulatedBackend("status-cache")
    instrument = NHR9300(
        "status-cache",
        backend,
        interlocks=[StaticInterlockProvider()],
    )
    instrument.connect()
    calls = 0
    original = instrument.read_status

    def counted_status():
        nonlocal calls
        calls += 1
        return original()

    instrument.read_status = counted_status  # type: ignore[method-assign]
    collector = AcquisitionCollector(
        instrument,
        rate_hz=10,
        csv_path=tmp_path / "status-cache.csv",
        status_refresh_interval_s=10,
    )
    collector.start()
    time.sleep(0.25)
    collector.set_context(step="next")
    time.sleep(0.2)
    collector.stop()
    instrument.close()

    assert collector.statistics().sample_count >= 4
    assert calls == 2


def test_runtime_interlock_failure_disables_output(tmp_path) -> None:
    interlock = StaticInterlockProvider()
    backend = SimulatedBackend("unsafe")
    instrument = NHR9300("unsafe", backend, interlocks=[interlock])
    instrument.connect()
    collector = AcquisitionCollector(
        instrument, rate_hz=10, csv_path=tmp_path / "unsafe.csv"
    )
    active = constant_current_hold(
        name="interlock-trip",
        limits=limits(),
        mode=OperatingState.DISCHARGE,
        current_a=1,
        voltage_v=300,
        power_w=500,
        duration_s=2,
        arm_duration_s=5,
    )
    runner = RoutineRunner(instrument, collector)
    runner.start(active)
    deadline = time.monotonic() + 1
    while not backend.enabled and time.monotonic() < deadline:
        time.sleep(0.01)
    interlock.safe = False
    runner._thread.join(timeout=2)
    status = instrument.read_status()
    instrument.close()
    assert runner.result.state == RoutineState.FAILED
    assert status.enabled is False
    assert status.state == OperatingState.STANDBY
