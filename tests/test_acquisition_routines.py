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
    with (tmp_path / "sim.csv").open(newline="", encoding="utf-8") as handle:
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
