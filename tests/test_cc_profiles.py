from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nhr9300 import (
    NHR9300,
    OperatingState,
    SafetyLimits,
    SimulatedBackend,
    StaticInterlockProvider,
)
from nhr9300.acquisition import AcquisitionCollector
from nhr9300.errors import NHRValidationError
from nhr9300.routines import (
    Condition,
    EnableStep,
    RoutineRunner,
    WaitStep,
    constant_current_hold,
)
from nhr9300.cc_profiles import load_cc_profile, validate_cc_profile
from nhr9300.types import Measurement, RoutineResult, RoutineState


def _cc_profile_data(
    *,
    mode: str = "charge",
    termination: dict | None = None,
    simulation_only: bool = False,
) -> dict:
    current_a = 1.0 if simulation_only else 5.0
    return {
        "test_description": "CC profile parser test",
        "bench_description": "REPLACE_WITH_REVIEWED_BENCH_DESCRIPTION",
        "stop_procedure": "REPLACE_WITH_REVIEWED_STOP_PROCEDURE",
        "expected_resource": "DC PM 1",
        "expected_serial_number": "REPLACE_WITH_EXPECTED_SERIAL_NUMBER",
        "simulation_initial_voltage_v": 90.0,
        "review_note": "Test fixture",
        "safety_limits": {
            "charge_current": 10.0,
            "charge_voltage_max": 100.0,
            "charge_power": 1000.0,
            "discharge_current": 10.0,
            "discharge_voltage_min": 75.0,
            "discharge_power": 1000.0,
            "current_delay_s": 0.1,
            "voltage_delay_s": 0.1,
            "power_delay_s": 0.1,
            "uut_temperature_max": None,
            "approved": False,
            "profile_name": "REPLACE_WITH_APPROVED_LIMITS",
        },
        "cc_hold": {
            "mode": mode,
            "current_a": current_a,
            "voltage_v": 75.0 if mode == "discharge" else 100.0,
            "power_w": 100.0 if simulation_only else 500.0,
            "max_duration_s": 5.0 if simulation_only else 10.0,
            "arm_duration_s": 10.0 if simulation_only else 15.0,
            "current_tolerance_a": 0.5,
            "current_settling_time_s": 0.0 if simulation_only else 0.5,
            "minimum_active_samples": 1 if simulation_only else 3,
            "termination": termination,
            "watchdog_enabled": not simulation_only,
            "ignore_uut_temperature": True,
            "approved": False,
            "profile_name": "REPLACE_WITH_APPROVED_CC_PROFILE",
            "simulation_only": simulation_only,
        },
    }


def _write_cc_profile(tmp_path: Path, **overrides) -> Path:
    path = tmp_path / "cc-profile.json"
    path.write_text(json.dumps(_cc_profile_data(**overrides)), encoding="utf-8")
    return path


def limits() -> SafetyLimits:
    return SafetyLimits(
        charge_current=1.0,
        charge_voltage_max=100.0,
        charge_power=100.0,
        discharge_current=1.0,
        discharge_voltage_min=75.0,
        discharge_power=100.0,
        uut_temperature_max=None,
        approved=True,
        profile_name="cc-test-limits",
    )


def run_condition(tmp_path, mode: OperatingState, condition: Condition):
    instrument = NHR9300(
        "condition-sim",
        SimulatedBackend("condition-sim", initial_voltage_v=90.0),
        interlocks=[StaticInterlockProvider()],
    )
    instrument.connect()
    collector = AcquisitionCollector(
        instrument, rate_hz=10, csv_path=tmp_path / "condition.csv"
    )
    routine = constant_current_hold(
        name="condition",
        limits=limits(),
        mode=mode,
        current_a=1.0,
        voltage_v=100.0 if mode == OperatingState.CHARGE else 75.0,
        power_w=100.0,
        duration_s=1.0,
        arm_duration_s=4.0,
        termination=condition,
    )
    result = RoutineRunner(instrument, collector).run(routine)
    status = instrument.read_status()
    instrument.close()
    return result, status


def test_cc_profile_loader_rejects_unapproved_profile(tmp_path) -> None:
    configuration = load_cc_profile(_write_cc_profile(tmp_path))
    assert configuration.safety_limits.approved is False
    assert configuration.cc_hold.approved is False
    with pytest.raises(NHRValidationError, match="approved"):
        validate_cc_profile(configuration, hardware=False)


@pytest.mark.parametrize(
    ("mode", "condition"),
    [
        (OperatingState.CHARGE, Condition("voltage", ">=", 90.0005)),
        (
            OperatingState.DISCHARGE,
            Condition("capacity_ah", ">=", 0.00002, relative=True),
        ),
        (
            OperatingState.CHARGE,
            Condition("energy_wh", ">=", 0.002, relative=True),
        ),
        (OperatingState.CHARGE, Condition("temperature", ">=", 25.001)),
    ],
)
def test_cc_termination_fields_are_observable(
    tmp_path, mode: OperatingState, condition: Condition
) -> None:
    result, status = run_condition(tmp_path, mode, condition)
    assert result.state == RoutineState.PASSED
    assert result.termination_reason == "condition"
    assert result.termination_field == condition.field
    assert result.termination_value is not None
    assert result.termination_measurement is not None
    if condition.relative:
        assert result.termination_baseline is not None
    assert status.enabled is False
    assert status.state == OperatingState.STANDBY


def test_unreached_condition_fails_instead_of_passing_on_timeout(tmp_path) -> None:
    instrument = NHR9300(
        "timeout-sim",
        SimulatedBackend("timeout-sim", initial_voltage_v=90.0),
        interlocks=[StaticInterlockProvider()],
    )
    instrument.connect()
    collector = AcquisitionCollector(
        instrument, rate_hz=10, csv_path=tmp_path / "timeout.csv"
    )
    routine = constant_current_hold(
        name="timeout",
        limits=limits(),
        mode=OperatingState.DISCHARGE,
        current_a=1.0,
        voltage_v=75.0,
        power_w=100.0,
        duration_s=0.2,
        arm_duration_s=3.0,
        termination=Condition("capacity_ah", ">=", 1.0, relative=True),
    )
    result = RoutineRunner(instrument, collector).run(routine)
    status = instrument.read_status()
    instrument.close()
    assert result.state == RoutineState.FAILED
    assert result.termination_reason == "condition_timeout"
    assert "was not reached" in result.reason
    assert status.enabled is False


def test_generic_discharge_counters_use_positive_throughput_magnitude() -> None:
    measurement = Measurement.now(
        "signed-nhr-counters",
        1.0,
        88.0,
        -5.0,
        -440.0,
        capacity_discharge_ah=-0.0125,
        energy_discharge_kwh=-0.00125,
    )

    capacity = Condition("capacity_ah", ">=", 0.01, relative=True)
    energy = Condition("energy_wh", ">=", 1.0, relative=True)

    assert capacity.measurement_value(measurement, OperatingState.DISCHARGE) == 0.0125
    assert energy.measurement_value(measurement, OperatingState.DISCHARGE) == 1.25


def test_wait_condition_is_evaluated_at_duration_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]

    class StopEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, seconds: float) -> None:
            clock[0] += seconds

    class Instrument:
        def check_interlocks(self) -> None:
            pass

        def read_measurement(self) -> Measurement:
            voltage_v = 88.5 if clock[0] >= 101.0 else 89.0
            return Measurement.now("boundary", clock[0], voltage_v, -5.0, -440.0)

    monkeypatch.setattr("nhr9300.routines.time.monotonic", lambda: clock[0])
    result = RoutineResult("boundary")
    context = SimpleNamespace(
        stop_event=StopEvent(),
        collector=SimpleNamespace(error=None),
        instrument=Instrument(),
        result=result,
    )

    WaitStep(
        duration_s=1.0,
        condition=Condition("voltage", "<=", 88.5),
        mode=OperatingState.DISCHARGE,
    ).execute(context)

    assert result.termination_reason == "condition"
    assert result.termination_value == 88.5


def test_duration_stop_is_reported_and_direct_enable_is_not_used(tmp_path) -> None:
    routine = constant_current_hold(
        name="duration",
        limits=limits(),
        mode=OperatingState.CHARGE,
        current_a=1.0,
        voltage_v=100.0,
        power_w=100.0,
        duration_s=0.15,
        arm_duration_s=3.0,
    )
    assert not any(isinstance(step, EnableStep) for step in routine.steps)
    instrument = NHR9300(
        "duration-sim",
        SimulatedBackend("duration-sim", initial_voltage_v=90.0),
        interlocks=[StaticInterlockProvider()],
    )
    instrument.connect()
    collector = AcquisitionCollector(
        instrument, rate_hz=10, csv_path=tmp_path / "duration.csv"
    )
    result = RoutineRunner(instrument, collector).run(routine)
    instrument.close()
    assert result.state == RoutineState.PASSED
    assert result.termination_reason == "duration"
    assert result.reason == "Configured duration elapsed"


def test_temperature_profile_is_simulation_only(tmp_path) -> None:
    configuration = load_cc_profile(
        _write_cc_profile(
            tmp_path,
            termination={
                "field": "temperature",
                "operator": ">=",
                "value": 25.001,
                "relative": False,
            },
            simulation_only=True,
        )
    )
    approved = replace(
        configuration,
        safety_limits=replace(
            configuration.safety_limits,
            approved=True,
            profile_name="approved-sim-limits",
        ),
        cc_hold=replace(
            configuration.cc_hold,
            approved=True,
            profile_name="approved-temperature-simulation",
        ),
    )
    validate_cc_profile(approved, hardware=False)
    with pytest.raises(NHRValidationError, match="simulation-only"):
        validate_cc_profile(approved, hardware=True)


def test_hardware_profile_requires_watchdog_and_respects_caps(tmp_path) -> None:
    configuration = load_cc_profile(_write_cc_profile(tmp_path))
    approved = replace(
        configuration,
        bench_description="Reviewed module bench",
        stop_procedure="Use the accessible emergency shutdown",
        expected_serial_number="SERIAL-613",
        safety_limits=replace(
            configuration.safety_limits,
            approved=True,
            profile_name="approved-hardware-limits",
        ),
        cc_hold=replace(
            configuration.cc_hold,
            approved=True,
            profile_name="approved-hardware-charge",
        ),
    )
    validate_cc_profile(approved, hardware=True)
    validate_cc_profile(
        replace(
            approved,
            cc_hold=replace(
                approved.cc_hold,
                max_duration_s=60.0,
                arm_duration_s=65.0,
            ),
        ),
        hardware=True,
    )
    with pytest.raises(NHRValidationError, match="requires the watchdog"):
        validate_cc_profile(
            replace(approved, cc_hold=replace(approved.cc_hold, watchdog_enabled=False)),
            hardware=True,
        )
    with pytest.raises(NHRValidationError, match="at most 5.0 A"):
        validate_cc_profile(
            replace(approved, cc_hold=replace(approved.cc_hold, current_a=5.1)),
            hardware=True,
        )
    with pytest.raises(NHRValidationError, match="at most 60.0 s"):
        validate_cc_profile(
            replace(
                approved,
                cc_hold=replace(
                    approved.cc_hold,
                    max_duration_s=60.1,
                    arm_duration_s=65.0,
                ),
            ),
            hardware=True,
        )
    with pytest.raises(NHRValidationError, match="at most 70.0 s"):
        validate_cc_profile(
            replace(
                approved,
                cc_hold=replace(
                    approved.cc_hold,
                    max_duration_s=60.0,
                    arm_duration_s=70.1,
                ),
            ),
            hardware=True,
        )
    with pytest.raises(NHRValidationError, match="between 0 and 2 s"):
        validate_cc_profile(
            replace(
                approved,
                cc_hold=replace(approved.cc_hold, current_settling_time_s=2.1),
            ),
            hardware=True,
        )
