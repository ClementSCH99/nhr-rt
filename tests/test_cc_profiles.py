from __future__ import annotations

import csv
import json
import sys
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
from scripts import supervised_cc_hold


TEMPLATES = sorted(Path("examples").glob("cc_*.example.json"))


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


def test_all_cc_templates_are_parseable_and_unapproved() -> None:
    assert len(TEMPLATES) == 5
    for path in TEMPLATES:
        configuration = load_cc_profile(path)
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


def test_temperature_profile_is_simulation_only() -> None:
    configuration = load_cc_profile(
        "examples/cc_temperature_simulation.example.json"
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


def test_hardware_profile_requires_watchdog_and_respects_session_caps() -> None:
    configuration = load_cc_profile(
        "examples/cc_charge_duration.example.json"
    )
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


def test_current_response_retains_transient_but_evaluates_after_settling(
    tmp_path,
) -> None:
    configuration = load_cc_profile(
        "examples/cc_charge_duration.example.json"
    )
    configuration = replace(
        configuration,
        cc_hold=replace(
            configuration.cc_hold,
            current_tolerance_a=0.5,
            current_settling_time_s=0.5,
            minimum_active_samples=3,
        ),
    )
    csv_path = tmp_path / "response.csv"
    fieldnames = ["monotonic_s", "step", "state", "current_a"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for elapsed_s, current_a in [
            (0.0, 4.2),
            (0.1, 4.9),
            (0.5, 4.99),
            (0.6, 5.01),
            (0.7, 5.0),
        ]:
            writer.writerow(
                {
                    "monotonic_s": 100.0 + elapsed_s,
                    "step": "03_wait",
                    "state": "charge",
                    "current_a": current_a,
                }
            )

    response = supervised_cc_hold._current_response(csv_path, configuration)

    assert response["passed"] is True
    assert response["active_sample_count"] == 5
    assert response["transient_sample_count"] == 2
    assert response["evaluated_sample_count"] == 3
    assert response["first_active_current_a"] == 4.2
    assert response["active_maximum_absolute_error_a"] == pytest.approx(0.8)
    assert response["maximum_absolute_error_a"] == pytest.approx(0.01)


def test_supervised_cc_runner_simulation_writes_safe_report(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = json.loads(
        Path("examples/cc_discharge_energy.example.json").read_text(
            encoding="utf-8"
        )
    )
    data["safety_limits"]["approved"] = True
    data["safety_limits"]["profile_name"] = "approved-runner-limits"
    data["cc_hold"]["approved"] = True
    data["cc_hold"]["profile_name"] = "approved-runner-energy"
    data["cc_hold"]["max_duration_s"] = 1.0
    data["cc_hold"]["arm_duration_s"] = 4.0
    data["cc_hold"]["current_settling_time_s"] = 0.0
    data["cc_hold"]["minimum_active_samples"] = 1
    data["cc_hold"]["termination"]["value"] = 0.002
    profile = tmp_path / "approved-cc.json"
    profile.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "results"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "supervised_cc_hold.py",
            "--simulate",
            "--profile",
            str(profile),
            "--output",
            str(output),
        ],
    )

    assert supervised_cc_hold.main() == 0
    reports = list(output.glob("*/report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["routine_result"]["termination_reason"] == "condition"
    assert report["routine_result"]["termination_field"] == "energy_wh"
    assert report["current_response"]["passed"] is True
    assert report["cleanup_status"]["enabled"] is False
    assert report["watchdog_after_reconnect"] is False
