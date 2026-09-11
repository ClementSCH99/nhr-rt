from __future__ import annotations

import csv
import hashlib
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nhr9300 import NHR9300, OperatingState, SafetyLimits, SimulatedBackend, StaticInterlockProvider
from nhr9300.acquisition import AcquisitionCollector
from nhr9300.sequences import (
    ProfilePoint,
    SequenceRunner,
    SequenceStage,
    dynamic_profile_routine,
    load_profile_csv,
)
from nhr9300.errors import NHRValidationError
from nhr9300.routines import (
    Condition,
    RoutineContext,
    RoutineRunner,
    WaitStep,
    constant_current_hold,
    constant_power_hold,
    rest_period,
)
from nhr9300.profiles import load_workflow_profile, validate_workflow_profile
from nhr9300.types import Measurement, RoutineResult, RoutineState
from nhr9300 import cli, execution


def limits() -> SafetyLimits:
    return SafetyLimits(
        charge_current=10,
        charge_voltage_max=100,
        charge_power=1000,
        discharge_current=10,
        discharge_voltage_min=80,
        discharge_power=1000,
        approved=True,
        profile_name="workflow-test",
    )


def setup(tmp_path, name="workflow", initial_voltage_v=90.0):
    instrument = NHR9300(
        name,
        SimulatedBackend(name, initial_voltage_v=initial_voltage_v),
        interlocks=[StaticInterlockProvider()],
    )
    instrument.connect()
    collector = AcquisitionCollector(instrument, rate_hz=10, csv_path=tmp_path / "measurements.csv")
    return instrument, collector


@pytest.mark.parametrize("mode,initial,voltage", [
    (OperatingState.CHARGE, 90.0, 90.02),
    (OperatingState.DISCHARGE, 90.0, 89.98),
])
def test_cccv_tapers_and_stops_on_cutoff_current(tmp_path, mode, initial, voltage) -> None:
    instrument, collector = setup(tmp_path, initial_voltage_v=initial)
    routine = constant_current_hold(
        name="cccv",
        limits=limits(),
        mode=mode,
        current_a=5.0,
        voltage_v=voltage,
        power_w=500.0,
        duration_s=6.0,
        arm_duration_s=10.0,
        termination=Condition("cutoff_current", "<=", 0.5),
    )
    result = RoutineRunner(instrument, collector).run(routine)
    status = instrument.read_status()
    instrument.close()

    assert result.state == RoutineState.PASSED
    assert result.termination_reason == "condition"
    assert result.termination_field == "cutoff_current"
    assert result.termination_value <= 0.5
    assert status.enabled is False


@pytest.mark.parametrize(
    "mode,gate,voltages",
    [
        (
            OperatingState.CHARGE,
            Condition("voltage", ">=", 90.0),
            (89.9, 89.9999999999997, 89.9999999999997),
        ),
        (
            OperatingState.DISCHARGE,
            Condition("voltage", "<=", 90.0),
            (90.1, 90.0000000000003, 90.0000000000003),
        ),
    ],
)
def test_cutoff_current_is_ignored_until_cv_voltage_is_reached(
    mode,
    gate,
    voltages,
) -> None:
    measurements = iter(
        Measurement.now("scripted", index, voltage, current, voltage * current)
        for index, (voltage, current) in enumerate(
            zip(voltages, (0.1, 5.0, 0.4))
        )
    )

    class ScriptedInstrument:
        def check_interlocks(self):
            return None

        def read_measurement(self):
            return next(measurements)

    result = RoutineResult(routine_id="cccv-gate")
    context = RoutineContext(
        instrument=ScriptedInstrument(),
        collector=SimpleNamespace(error=None),
        stop_event=threading.Event(),
        result=result,
    )
    WaitStep(
        duration_s=1.0,
        condition=Condition("cutoff_current", "<=", 0.5),
        poll_interval_s=0.0,
        mode=mode,
        activation_condition=gate,
        activation_tolerance=1e-6,
    ).execute(context)

    assert result.termination_reason == "condition"
    assert result.termination_value == pytest.approx(0.4)


@pytest.mark.parametrize("mode", [OperatingState.CHARGE, OperatingState.DISCHARGE])
def test_constant_power_tracks_requested_magnitude(tmp_path, mode) -> None:
    instrument, collector = setup(tmp_path)
    routine = constant_power_hold(
        name="cp",
        limits=limits(),
        mode=mode,
        power_w=180.0,
        voltage_limit_v=99.0 if mode == OperatingState.CHARGE else 81.0,
        current_limit_a=5.0,
        duration_s=0.4,
        arm_duration_s=5.0,
    )
    result = RoutineRunner(instrument, collector).run(routine)
    instrument.close()

    assert result.state == RoutineState.PASSED
    assert result.termination_measurement is not None
    assert abs(result.termination_measurement.power_w) == pytest.approx(180.0, rel=0.01)


def test_optional_operating_limits_disable_only_requested_channels(tmp_path) -> None:
    instrument, collector = setup(tmp_path)
    cc_result = RoutineRunner(instrument, collector).run(
        constant_current_hold(
            name="cc-without-power-limit",
            limits=limits(),
            mode=OperatingState.CHARGE,
            current_a=2,
            voltage_v=99,
            power_w=500,
            power_limit_enabled=False,
            duration_s=0.2,
            arm_duration_s=5,
        )
    )
    cc_status = instrument.read_status()
    cp_result = RoutineRunner(instrument, collector).run(
        constant_power_hold(
            name="cp-without-current-limit",
            limits=limits(),
            mode=OperatingState.DISCHARGE,
            power_w=180,
            voltage_limit_v=81,
            current_limit_a=5,
            current_limit_enabled=False,
            duration_s=0.2,
            arm_duration_s=5,
            configure_limits=False,
        )
    )
    cp_status = instrument.read_status()
    instrument.close()

    assert cc_result.state == RoutineState.PASSED
    assert cc_status.setpoints.voltage_enabled is True
    assert cc_status.setpoints.current_enabled is True
    assert cc_status.setpoints.power_enabled is False
    assert cp_result.state == RoutineState.PASSED
    assert cp_status.setpoints.voltage_enabled is True
    assert cp_status.setpoints.current_enabled is False
    assert cp_status.setpoints.power_enabled is True


@pytest.mark.parametrize(
    "mode,condition",
    [
        (OperatingState.CHARGE, Condition("voltage", ">=", 90.001)),
        (OperatingState.DISCHARGE, Condition("voltage", "<=", 89.999)),
        (OperatingState.CHARGE, Condition("capacity_ah", ">=", 0.00002, relative=True)),
        (OperatingState.DISCHARGE, Condition("capacity_ah", ">=", 0.00002, relative=True)),
        (OperatingState.CHARGE, Condition("energy_wh", ">=", 0.002, relative=True)),
        (OperatingState.DISCHARGE, Condition("energy_wh", ">=", 0.002, relative=True)),
    ],
)
def test_constant_power_supports_voltage_capacity_and_energy_stops(tmp_path, mode, condition) -> None:
    instrument, collector = setup(tmp_path)
    result = RoutineRunner(instrument, collector).run(
        constant_power_hold(
            name="cp-stop",
            limits=limits(),
            mode=mode,
            power_w=180,
            voltage_limit_v=99 if mode == OperatingState.CHARGE else 81,
            current_limit_a=5,
            duration_s=1.0,
            arm_duration_s=5,
            termination=condition,
        )
    )
    instrument.close()

    assert result.state == RoutineState.PASSED
    assert result.termination_reason == "condition"
    assert result.termination_field == condition.field


def test_rest_is_measured_and_disabled(tmp_path) -> None:
    instrument, collector = setup(tmp_path)
    result = RoutineRunner(instrument, collector).run(rest_period(name="rest", duration_s=0.2))
    status = instrument.read_status()
    instrument.close()

    assert result.state == RoutineState.PASSED
    assert result.termination_reason == "duration"
    assert status.state == OperatingState.STANDBY
    assert status.enabled is False


def test_sequence_stops_in_order_and_aggregates_directional_counters(tmp_path) -> None:
    instrument, collector = setup(tmp_path)
    stages = (
        SequenceStage(
            "charge",
            constant_current_hold(
                name="charge", limits=limits(), mode=OperatingState.CHARGE,
                    current_a=2, voltage_v=99, power_w=500, duration_s=1.0,
                arm_duration_s=5,
            ),
            OperatingState.CHARGE,
        ),
        SequenceStage("rest", rest_period(name="rest", duration_s=0.15)),
        SequenceStage(
            "discharge",
            constant_current_hold(
                name="discharge", limits=limits(), mode=OperatingState.DISCHARGE,
                    current_a=2, voltage_v=81, power_w=500, duration_s=1.0,
                arm_duration_s=5, configure_limits=False,
            ),
            OperatingState.DISCHARGE,
        ),
    )
    result = SequenceRunner(instrument, collector).run(stages)
    status = instrument.read_status()
    instrument.close()

    assert result.state == RoutineState.PASSED
    assert [stage.state for stage in result.stages] == [RoutineState.PASSED] * 3
    assert len({stage.csv_path for stage in result.stages}) == 3
    assert result.global_csv_path is not None
    assert result.global_csv_path not in {stage.csv_path for stage in result.stages}
    assert result.global_acquisition is not None
    assert result.global_acquisition.sample_count >= sum(result.stage_sample_counts)
    with open(result.global_csv_path, newline="", encoding="utf-8") as handle:
        global_rows = list(csv.DictReader(handle))
    assert {row["routine_id"] for row in global_rows if row["routine_id"]} == {
        stage.routine_id for stage in result.stages
    }
    for stage_result, expected_count in zip(result.stages, result.stage_sample_counts):
        with open(stage_result.csv_path, newline="", encoding="utf-8") as handle:
            stage_rows = list(csv.DictReader(handle))
        assert len(stage_rows) == expected_count
        assert {row["routine_id"] for row in stage_rows} == {stage_result.routine_id}
    assert result.capacity_charge_ah > 0
    assert result.capacity_discharge_ah > 0
    assert result.energy_charge_wh > 0
    assert result.energy_discharge_wh > 0
    assert status.enabled is False


def test_signed_csv_profile_runs_charge_discharge_and_rest(tmp_path) -> None:
    profile = tmp_path / "current.csv"
    profile.write_text("time_s,current_a\n0,2\n0.4,-1\n0.8,0\n1.2,0\n", encoding="utf-8")
    points = load_profile_csv(profile, "current")
    instrument, collector = setup(tmp_path)
    routine = dynamic_profile_routine(
        name="dynamic",
        limits=limits(),
        points=points,
        kind="current",
        charge_voltage_limit_v=99,
        discharge_voltage_limit_v=81,
        current_limit_a=5,
        power_limit_w=500,
    )
    result = RoutineRunner(instrument, collector).run(routine)
    status = instrument.read_status()
    path = collector.csv_path
    instrument.close()

    assert result.state == RoutineState.PASSED
    assert result.termination_reason == "profile_end"
    assert status.enabled is False
    assert status.state == OperatingState.STANDBY
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    states = {row["state"] for row in rows}
    assert {"charge", "discharge"}.issubset(states)
    zero_segment = [row for row in rows if row["step"] == "dynamic_00002"]
    assert zero_segment
    assert {row["state"] for row in zero_segment} == {"discharge"}
    assert {float(row["setpoint_current_a"]) for row in zero_segment} == {0.0}


def test_signed_profile_does_not_disable_between_active_directions(tmp_path) -> None:
    class TrackingBackend(SimulatedBackend):
        def __init__(self):
            super().__init__("tracking", initial_voltage_v=90.0)
            self.disable_count = 0

        def set_enabled(self, enabled):
            if not enabled:
                self.disable_count += 1
            super().set_enabled(enabled)

    backend = TrackingBackend()
    instrument = NHR9300(
        "tracking",
        backend,
        interlocks=[StaticInterlockProvider()],
    )
    instrument.connect()
    collector = AcquisitionCollector(
        instrument,
        rate_hz=10,
        csv_path=tmp_path / "direction-change.csv",
    )
    routine = dynamic_profile_routine(
        name="direct-direction-change",
        limits=limits(),
        points=(
            ProfilePoint(0.0, 2.0),
            ProfilePoint(0.2, 0.0),
            ProfilePoint(0.4, -2.0),
            ProfilePoint(0.6, 0.0),
            ProfilePoint(0.8, 0.0),
        ),
        kind="current",
        charge_voltage_limit_v=99,
        discharge_voltage_limit_v=81,
        current_limit_a=5,
        power_limit_w=500,
    )

    result = RoutineRunner(instrument, collector).run(routine)
    instrument.close()

    assert result.state == RoutineState.PASSED
    # One disable before the first active state and one at final cleanup only.
    assert backend.disable_count == 2


def test_workflow_contract_builds_all_stage_types(tmp_path) -> None:
    csv_path = tmp_path / "profile.csv"
    csv_path.write_text("time_s,power_w\n0,100\n0.2,-100\n0.4,0\n", encoding="utf-8")
    data = {
        "test_description": "simulation",
        "bench_description": "reviewed bench",
        "stop_procedure": "press stop",
        "expected_resource": "DC PM 1",
        "expected_serial_number": "SERIAL-613",
        "simulation_initial_voltage_v": 90,
        "watchdog_enabled": True,
        "safety_limits": {
            "charge_current": 10, "charge_voltage_max": 100, "charge_power": 1000,
            "discharge_current": 10, "discharge_voltage_min": 80, "discharge_power": 1000,
            "approved": True, "profile_name": "approved-limits"
        },
        "workflow_limits": {
            "max_current_a": 5, "max_power_w": 500, "max_stage_duration_s": 10,
            "max_sequence_duration_s": 30, "approved": True, "profile_name": "approved-workflow"
        },
        "stages": [
            {"name": "cccv", "type": "cccv", "duration_s": 2, "mode": "charge", "current_a": 2, "voltage_v": 95, "power_w": 300, "voltage_limit_enabled": True, "current_limit_enabled": True, "power_limit_enabled": False, "cutoff_current_a": 0.2},
            {"name": "rest", "type": "rest", "duration_s": 1},
            {"name": "cp", "type": "constant_power", "duration_s": 2, "mode": "discharge", "current_a": 5, "voltage_v": 82, "power_w": 200, "voltage_limit_enabled": True, "current_limit_enabled": True, "power_limit_enabled": True},
            {"name": "csv", "type": "csv_profile", "duration_s": 0.4, "csv_path": "profile.csv", "profile_kind": "power", "current_a": 5, "power_w": 500, "voltage_limit_enabled": True, "current_limit_enabled": True, "power_limit_enabled": True, "charge_voltage_limit_v": 98, "discharge_voltage_limit_v": 82}
        ]
    }
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    configuration = load_workflow_profile(path)

    validate_workflow_profile(configuration, hardware=False)
    validate_workflow_profile(configuration, hardware=True)
    sequence = configuration.sequence(configure_limits=True)
    assert [stage.name for stage in sequence] == ["cccv", "rest", "cp", "csv"]
    cccv_wait = next(
        step for step in sequence[0].routine.steps if isinstance(step, WaitStep)
    )
    assert cccv_wait.activation_condition == Condition("voltage", ">=", 95.0)
    assert cccv_wait.activation_tolerance == pytest.approx(1e-6)

    bad = replace(configuration, workflow_limits=replace(configuration.workflow_limits, max_current_a=1))
    with pytest.raises(NHRValidationError, match="workflow limits"):
        validate_workflow_profile(bad, hardware=False)

    missing_cutoff = replace(
        configuration,
        stages=(replace(configuration.stages[0], cutoff_current_a=None),)
    )
    with pytest.raises(NHRValidationError, match="cutoff_current_a"):
        validate_workflow_profile(missing_cutoff, hardware=False)

    no_limit = replace(
        configuration,
        stages=(
            replace(
                configuration.stages[0],
                voltage_limit_enabled=False,
                current_limit_enabled=False,
                power_limit_enabled=False,
            ),
        ),
    )
    with pytest.raises(NHRValidationError, match="at least one operating limit"):
        validate_workflow_profile(no_limit, hardware=False)

    cc_without_power_limit = replace(
        configuration,
        stages=(
            replace(
                configuration.stages[0],
                type="constant_current",
                cutoff_current_a=None,
                power_limit_enabled=False,
            ),
        ),
    )
    validate_workflow_profile(cc_without_power_limit, hardware=False)

    long_stage = replace(
        configuration,
        workflow_limits=replace(
            configuration.workflow_limits,
            max_stage_duration_s=28_800,
            max_sequence_duration_s=43_200,
        ),
        stages=(replace(configuration.stages[0], duration_s=28_800),),
    )
    with pytest.raises(NHRValidationError, match="cannot exceed 295 s"):
        validate_workflow_profile(long_stage, hardware=False)

    renewable = replace(
        long_stage,
        workflow_limits=replace(
            long_stage.workflow_limits,
            arm_lease_renewal_enabled=True,
        ),
    )
    validate_workflow_profile(renewable, hardware=False)

    with pytest.raises(NHRValidationError, match="watchdog_enabled=true"):
        validate_workflow_profile(
            replace(renewable, watchdog_enabled=False), hardware=False
        )

    excessive_stage = replace(
        renewable,
        workflow_limits=replace(
            renewable.workflow_limits,
            max_stage_duration_s=28_800.1,
        ),
        stages=(replace(renewable.stages[0], duration_s=28_800.1),),
    )
    with pytest.raises(NHRValidationError, match="cannot exceed 28800 s"):
        validate_workflow_profile(excessive_stage, hardware=False)

    excessive_sequence = replace(
        renewable,
        workflow_limits=replace(
            renewable.workflow_limits,
            max_sequence_duration_s=43_200.1,
        ),
    )
    with pytest.raises(NHRValidationError, match="cannot exceed 43200 s"):
        validate_workflow_profile(excessive_sequence, hardware=False)


def test_generic_workflow_examples_are_valid_but_unapproved() -> None:
    for path in Path("examples/workflows").glob("*.example.json"):
        configuration = load_workflow_profile(path)
        assert configuration.safety_limits.approved is False
        assert configuration.workflow_limits.approved is False
        reviewed = replace(
            configuration,
            safety_limits=replace(configuration.safety_limits, approved=True),
            workflow_limits=replace(configuration.workflow_limits, approved=True),
        )
        validate_workflow_profile(reviewed, hardware=False)


def test_workflow_cli_simulation_writes_a_safe_report(tmp_path, monkeypatch) -> None:
    profile = tmp_path / "workflow.json"
    data = {
        "test_description": "runner simulation",
        "bench_description": "reviewed bench",
        "stop_procedure": "press stop",
        "expected_resource": "DC PM 1",
        "expected_serial_number": "SERIAL-613",
        "simulation_initial_voltage_v": 90,
        "watchdog_enabled": True,
        "safety_limits": {
            "charge_current": 10, "charge_voltage_max": 100, "charge_power": 1000,
            "discharge_current": 10, "discharge_voltage_min": 80, "discharge_power": 1000,
            "approved": True, "profile_name": "approved-limits"
        },
        "workflow_limits": {
            "max_current_a": 5, "max_power_w": 500, "max_stage_duration_s": 10,
            "max_sequence_duration_s": 20, "approved": True, "profile_name": "approved-workflow"
        },
        "stages": [
            {"name": "rest", "type": "rest", "duration_s": 0.1},
            {"name": "cp", "type": "constant_power", "duration_s": 0.3, "mode": "discharge", "current_a": 5, "voltage_v": 82, "power_w": 180, "voltage_limit_enabled": True, "current_limit_enabled": False, "power_limit_enabled": True}
        ]
    }
    profile.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "results"
    monkeypatch.setattr(sys, "argv", [
        "nhr9300-run", "--simulate", "--profile", str(profile),
        "--output", str(output),
    ])

    assert cli.main() == 0
    reports = list(output.glob("*/report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["sequence_result"]["state"] == "passed"
    assert len(report["sequence_result"]["stages"]) == 2
    assert report["sequence_result"]["global_csv_path"]
    assert len(report["sequence_result"]["stage_sample_counts"]) == 2
    assert report["cleanup_status"]["enabled"] is False
    assert report["watchdog_after_reconnect"] is False
    assert report["dynamic_profile_files"] == []


def test_standalone_runner_rejects_arm_lease_renewal(tmp_path) -> None:
    profile = tmp_path / "workflow.json"
    data = {
        "test_description": "service-only renewal",
        "bench_description": "simulator",
        "stop_procedure": "service-owned stop",
        "expected_resource": "sim",
        "expected_serial_number": "SIM-9300",
        "simulation_initial_voltage_v": 90,
        "watchdog_enabled": True,
        "safety_limits": {
            "charge_current": 5, "charge_voltage_max": 100, "charge_power": 500,
            "discharge_current": 5, "discharge_voltage_min": 80, "discharge_power": 500,
            "approved": True, "profile_name": "approved-limits"
        },
        "workflow_limits": {
            "max_current_a": 2, "max_power_w": 250,
            "max_stage_duration_s": 10_800,
            "max_sequence_duration_s": 10_800,
            "arm_lease_renewal_enabled": True,
            "approved": True, "profile_name": "approved-long-workflow"
        },
        "stages": [{"name": "rest", "type": "rest", "duration_s": 1}],
    }
    profile.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="only through the 32-bit service"):
        execution.execute_workflow(
            execution.WorkflowRequest(profile=profile, output=tmp_path / "results")
        )


def test_dynamic_profile_evidence_hashes_exact_csv_bytes(tmp_path) -> None:
    profile = tmp_path / "dynamic.csv"
    profile.write_bytes(b"time_s,current_a\n0,1\n1,0\n")
    configuration = SimpleNamespace(
        stages=(
            SimpleNamespace(
                name="dynamic-current",
                type="csv_profile",
                csv_path=profile,
            ),
        )
    )

    evidence = execution._dynamic_profile_evidence(configuration)

    assert evidence == [
        {
            "stage": "dynamic-current",
            "path": str(profile.resolve()),
            "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
            "size_bytes": profile.stat().st_size,
        }
    ]
