"""Software checks for the unchanged, approved Orca duty instructions."""

from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nhr9300 import NHR9300, SimulatedBackend, StaticInterlockProvider
from nhr9300.acquisition import AcquisitionCollector
from nhr9300.arm_lease import ArmLeaseSupervisor
from nhr9300.errors import NHRValidationError
from nhr9300.external_interlocks import ExternalInterlockManager
from nhr9300.profiles import load_workflow_profile, validate_workflow_profile
from nhr9300.sequences import SequenceRunner
from nhr9300.types import RoutineState
from nhr9300.workflow_registry import WorkflowBundle


APPROVED = Path(__file__).resolve().parents[1] / "approved/workflow/orca_duty_mod.json"
pytestmark = pytest.mark.skipif(
    not APPROVED.is_file() or not (APPROVED.parent / "profiles/orca_duty_mod.csv").is_file(),
    reason="Local approved Orca duty bundle is unavailable",
)


def _short_profile(tmp_path: Path, *, charge_value: float | None = None) -> Path:
    data = json.loads(APPROVED.read_text(encoding="utf-8"))
    # Keep the instructions and limits; shorten only this isolated simulation.
    for stage in data["stages"]:
        stage["duration_s"] = 0.25
    data["workflow_limits"]["post_sequence_rest_s"] = 0
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    csv_path = tmp_path / "profiles/orca_duty_mod.csv"
    csv_path.parent.mkdir()
    first = charge_value if charge_value is not None else -4375
    csv_path.write_text(f"time_s,power_w\n0,{first}\n0.125,0\n0.25,0\n", encoding="utf-8")
    return path


def _bms(manager: ExternalInterlockManager, *, min_v=3.0, max_temp=30.0, complete=True) -> None:
    signals = {"MinCellVolt": min_v, "MaxCellTemp": max_temp}
    if complete:
        signals.update({"MinCellTemp": 20.0, "MaxCellVolt": 4.0})
    manager.submit("bms-poc-v2", {
        "sequence": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "health": "ok",
        "signals": signals,
    })


def _run(tmp_path: Path, *, min_v=3.0, max_temp=30.0, submit=True, complete=True):
    configuration = load_workflow_profile(_short_profile(tmp_path))
    validate_workflow_profile(configuration, hardware=False)
    interlocks = ExternalInterlockManager()
    interlocks.activate(configuration.external_interlocks, phase="runtime", latch_runtime=True)
    if submit:
        _bms(interlocks, min_v=min_v, max_temp=max_temp, complete=complete)
    instrument = NHR9300(
        "orca-sim", SimulatedBackend("orca-sim", initial_voltage_v=90.0),
        interlocks=[StaticInterlockProvider(), interlocks],
    )
    instrument.connect()
    collector = AcquisitionCollector(instrument, rate_hz=10, csv_path=tmp_path / "session.csv")
    try:
        result = SequenceRunner(
            instrument, collector,
            external_signal_reader=interlocks.read_termination_signal,
        ).run(configuration.sequence(configure_limits=True)[:3])
        status = instrument.read_status()
    finally:
        instrument.close()
    return result, status


def test_approved_orca_bundle_reuses_one_immutable_csv(tmp_path) -> None:
    bundle = WorkflowBundle.load(APPROVED, hardware=False)
    assert WorkflowBundle.load(APPROVED, hardware=True).digest == bundle.digest
    assert len(bundle.files) == 2
    assert len(bundle.configuration.stages) == 3
    assert len({stage.csv_path for stage in bundle.configuration.stages}) == 1
    bundle.verify_unchanged()
    materialized = bundle.materialize(tmp_path / "snapshot")
    assert materialized.read_bytes() == APPROVED.read_bytes()
    assert (materialized.parent / "profiles/orca_duty_mod.csv").read_bytes() == (
        APPROVED.parent / "profiles/orca_duty_mod.csv"
    ).read_bytes()
    assert WorkflowBundle.load(materialized, hardware=False).digest == bundle.digest


def test_three_orca_passes_finish_at_csv_end_with_bms_healthy(tmp_path) -> None:
    result, status = _run(tmp_path)
    assert result.state == RoutineState.PASSED, result.reason
    assert [stage.termination_reason for stage in result.stages] == ["profile_end"] * 3
    assert all(stage.termination_measurement is not None for stage in result.stages)
    assert status.enabled is False
    with Path(result.global_csv_path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    zero_rows = [row for row in rows if row["step"] == "dynamic_00001"]
    assert zero_rows
    assert {row["state"] for row in zero_rows} == {"discharge"}
    assert {float(row["setpoint_power_w"]) for row in zero_rows} == {0.0}


@pytest.mark.parametrize("min_v,max_temp,field", [
    (2.75, 30.0, "MinCellVolt"),
    (3.0, 55.0, "MaxCellTemp"),
])
def test_orca_normal_bms_threshold_ends_first_stage(tmp_path, min_v, max_temp, field) -> None:
    result, status = _run(tmp_path, min_v=min_v, max_temp=max_temp)
    assert result.state == RoutineState.PASSED, result.reason
    assert result.stages[0].termination_reason == "condition"
    assert result.stages[0].termination_field == field
    assert result.stages[0].termination_detail["trigger"]["source_sequence"] == 1
    assert status.enabled is False


@pytest.mark.parametrize("submit,complete", [(False, False), (True, False)])
def test_orca_missing_bms_data_fails_closed(tmp_path, submit, complete) -> None:
    result, _status = _run(tmp_path, submit=submit, complete=complete)
    assert result.state == RoutineState.FAILED
    assert result.stages[0].termination_reason is None


def test_orca_extreme_bms_threshold_preempts_normal_termination(tmp_path) -> None:
    result, _status = _run(tmp_path, min_v=2.64)
    assert result.state == RoutineState.FAILED
    assert result.stages[0].termination_reason is None


def test_signed_csv_checks_only_present_direction_and_rejects_charge_over_limit(tmp_path) -> None:
    path = _short_profile(tmp_path, charge_value=9000)
    configuration = load_workflow_profile(path)
    with pytest.raises(NHRValidationError, match="safety limits"):
        validate_workflow_profile(configuration, hardware=False)


def test_accelerated_orca_profile_renews_with_approved_changes(tmp_path) -> None:
    """Run every approved Orca point in simulation with scaled time only."""
    data = json.loads(APPROVED.read_text(encoding="utf-8"))
    for stage in data["stages"]:
        stage["duration_s"] = 1.01
    data["workflow_limits"]["post_sequence_rest_s"] = 0
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    source = APPROVED.parent / "profiles/orca_duty_mod.csv"
    destination = tmp_path / "profiles/orca_duty_mod.csv"
    destination.parent.mkdir()
    with source.open(newline="", encoding="utf-8") as handle:
        points = list(csv.DictReader(handle))
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["time_s", "power_w"])
        writer.writeheader()
        writer.writerows(
            {"time_s": float(point["time_s"]) / 1000, "power_w": point["power_w"]}
            for point in points
        )
    configuration = load_workflow_profile(path)
    validate_workflow_profile(configuration, hardware=False)
    interlocks = ExternalInterlockManager()
    interlocks.activate(configuration.external_interlocks, phase="runtime", latch_runtime=True)
    _bms(interlocks)
    feed_stop = threading.Event()

    def feed_bms() -> None:
        sequence = 2
        while not feed_stop.wait(0.25):
            interlocks.submit("bms-poc-v2", {
                "sequence": sequence,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "health": "ok",
                "signals": {"MinCellVolt": 3.0, "MaxCellTemp": 30.0,
                            "MinCellTemp": 20.0, "MaxCellVolt": 4.0},
            })
            sequence += 1

    feed_thread = threading.Thread(target=feed_bms, daemon=True)
    feed_thread.start()
    instrument = NHR9300(
        "orca-sim", SimulatedBackend("orca-sim", initial_voltage_v=90.0),
        interlocks=[StaticInterlockProvider(), interlocks],
    )
    instrument.connect()
    instrument.configure_safety_limits(configuration.safety_limits)
    instrument.set_watchdog(True)
    collector = AcquisitionCollector(instrument, rate_hz=10, csv_path=tmp_path / "session.csv")
    supervisor = ArmLeaseSupervisor(
        run_id="orca-sim", workflow_id="mod-orca-duty-v1", bundle_digest="simulation",
        instrument=instrument, collector=collector,
        safety_limits=configuration.safety_limits, max_sequence_duration_s=10,
        stop_event=threading.Event(), renewal_threshold_s=0,
        renewal_margin_s=301,
    )
    supervisor.start_sequence()
    try:
        result = SequenceRunner(
            instrument, collector, arm_lease_supervisor=supervisor,
            external_signal_reader=interlocks.read_termination_signal,
        ).run(configuration.sequence(configure_limits=False)[:3])
        assert result.state == RoutineState.PASSED, result.reason
        assert len(result.stages) == 3
        assert supervisor.snapshot()["renewal_count"] == 0
        applied = [event for event in supervisor.snapshot()["events"]
                   if event["decision"] == "profile_point_applied"]
        assert len(applied) == 3 * (len(points) - 1)
        assert instrument.read_status().enabled is False
    finally:
        feed_stop.set()
        feed_thread.join(timeout=1)
        supervisor.close("simulation_complete")
        instrument.close()
