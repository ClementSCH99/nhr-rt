from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from nhr9300.acquisition import AcquisitionCollector
from nhr9300.arm_lease import (
    ArmLeaseExpiredError,
    ArmLeaseRenewalError,
    ArmLeaseSupervisor,
)
from nhr9300.backends.simulator import SimulatedBackend
from nhr9300.external_interlocks import ExternalInterlockManager, ExternalInterlockRule
from nhr9300.instrument import NHR9300
from nhr9300.interlocks import StaticInterlockProvider
from nhr9300.qualification import safety_limit_mismatches
from nhr9300.routines import rest_period
from nhr9300.sequences import DynamicProfileStep, ProfilePoint
from nhr9300.sequences import SequenceRunner, SequenceStage
from nhr9300.types import OperatingState, RoutineState, SafetyLimits, Setpoints


def _limits() -> SafetyLimits:
    return SafetyLimits(
        charge_current=10,
        charge_voltage_max=100,
        charge_power=1000,
        discharge_current=10,
        discharge_voltage_min=80,
        discharge_power=1000,
        approved=True,
        profile_name="approved-test-limits",
    )


def _active_runtime():
    interlock = StaticInterlockProvider()
    backend = SimulatedBackend("lease", initial_voltage_v=90)
    instrument = NHR9300("lease", backend, interlocks=[interlock])
    instrument.connect()
    limits = _limits()
    instrument.configure_safety_limits(limits)
    instrument.set_watchdog(True)
    instrument.arm(50)
    measurement = instrument.read_measurement()
    instrument.configure_setpoints(
        Setpoints(
            state=OperatingState.CHARGE,
            voltage=99,
            current=2,
            power=500,
            voltage_enabled=True,
            current_enabled=True,
            power_enabled=False,
            control_mode="current",
        )
    )
    collector = SimpleNamespace(running=True, error=None)
    stop_event = threading.Event()
    supervisor = ArmLeaseSupervisor(
        run_id="run-1",
        workflow_id="workflow-1",
        bundle_digest="sha256:test",
        instrument=instrument,
        collector=collector,
        safety_limits=limits,
        max_sequence_duration_s=1000,
        stop_event=stop_event,
    )
    supervisor.start_sequence()
    supervisor.begin_stage(
        index=0,
        name="long-cc",
        stage_type="constant_current",
        duration_s=1000,
    )
    return instrument, backend, interlock, collector, stop_event, supervisor, measurement


def _close(instrument: NHR9300) -> None:
    if instrument.connected:
        instrument.disable()
        instrument.set_watchdog(False)
        instrument.close()


@pytest.mark.parametrize(
    "stage_type", ["constant_current", "cccv", "constant_power"]
)
def test_nominal_service_owned_renewal_preserves_setpoints_and_limits(
    stage_type: str,
) -> None:
    instrument, _, _, _, _, supervisor, measurement = _active_runtime()
    supervisor.begin_stage(
        index=0,
        name=f"long-{stage_type}",
        stage_type=stage_type,
        duration_s=1000,
    )
    before = instrument.read_status().setpoints
    try:
        supervisor.checkpoint(measurement)
        snapshot = supervisor.snapshot()
        assert snapshot["renewal_count"] == 1
        assert snapshot["events"][-1]["decision"] == "renewed"
        assert instrument.read_status().setpoints == before
        assert not safety_limit_mismatches(_limits(), instrument.read_safety_limits())
    finally:
        _close(instrument)


@pytest.mark.parametrize("failure", ["watchdog", "interlock", "acquisition"])
def test_renewal_refuses_unsafe_runtime(failure: str) -> None:
    instrument, _, interlock, collector, _, supervisor, measurement = _active_runtime()
    try:
        if failure == "watchdog":
            instrument.set_watchdog(False)
        elif failure == "interlock":
            interlock.safe = False
        else:
            collector.error = "forced acquisition fault"
        with pytest.raises(ArmLeaseRenewalError):
            supervisor.checkpoint(measurement)
        assert supervisor.snapshot()["events"][-1]["decision"] == "refused"
    finally:
        interlock.safe = True
        _close(instrument)


def test_renewal_refuses_stale_nhr_measurement() -> None:
    instrument, _, _, _, _, supervisor, measurement = _active_runtime()
    stale = replace(measurement, monotonic_s=time.monotonic() - 10)
    try:
        with pytest.raises(ArmLeaseRenewalError, match="stale"):
            supervisor.checkpoint(stale)
        assert supervisor.snapshot()["events"][-1]["decision"] == "refused"
    finally:
        _close(instrument)


def test_renewal_failure_is_recorded_fail_closed(monkeypatch) -> None:
    instrument, _, _, _, _, supervisor, measurement = _active_runtime()
    monkeypatch.setattr(
        instrument,
        "renew_arm",
        lambda _duration: (_ for _ in ()).throw(RuntimeError("forced renewal failure")),
    )
    try:
        with pytest.raises(ArmLeaseRenewalError, match="forced renewal failure"):
            supervisor.checkpoint(measurement)
        event = supervisor.snapshot()["events"][-1]
        assert event["decision"] == "refused"
        assert "forced renewal failure" in event["reason"]
    finally:
        _close(instrument)


def test_operator_stop_during_renewal_revokes_authority(monkeypatch) -> None:
    instrument, _, _, _, stop_event, supervisor, measurement = _active_runtime()
    original = instrument.renew_arm

    def stop_while_renewing(duration: float) -> float:
        expiry = original(duration)
        stop_event.set()
        return expiry

    monkeypatch.setattr(instrument, "renew_arm", stop_while_renewing)
    try:
        with pytest.raises(InterruptedError):
            supervisor.checkpoint(measurement)
        decisions = [item["decision"] for item in supervisor.snapshot()["events"]]
        assert decisions[-2:] == ["renewed", "revoked"]
    finally:
        _close(instrument)


def test_expired_lease_is_recorded() -> None:
    instrument, _, _, _, _, supervisor, measurement = _active_runtime()
    supervisor.clock = lambda: time.monotonic() + 100
    try:
        with pytest.raises(ArmLeaseExpiredError):
            supervisor.checkpoint(measurement)
        assert supervisor.snapshot()["events"][-1]["decision"] == "expired"
    finally:
        _close(instrument)


@pytest.mark.parametrize("fault", ["unsafe", "stale", "health_fault"])
def test_can_snapshot_faults_refuse_renewal(fault: str) -> None:
    instrument, _, _, _, _, supervisor, measurement = _active_runtime()
    external = ExternalInterlockManager()
    rule = ExternalInterlockRule(
        rule_id="can-permissive",
        source_id="can",
        signal="safe",
        comparison="equals",
        unit="bool",
        applies="runtime",
        max_age_s=0.02,
        expected=True,
    )
    external.submit(
        "can",
        {
            "sequence": 1,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "health": "ok",
            "signals": {"safe": True},
        },
    )
    external.activate((rule,), phase="runtime", latch_runtime=True)
    instrument._interlocks.append(external)
    supervisor.renewal_margin_s = 301
    try:
        if fault == "unsafe":
            external.submit(
                "can",
                {
                    "sequence": 2,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "health": "ok",
                    "signals": {"safe": False},
                },
            )
        elif fault == "health_fault":
            external.submit(
                "can",
                {
                    "sequence": 2,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "health": "fault",
                    "signals": {"safe": True},
                },
            )
        else:
            time.sleep(0.03)

        with pytest.raises(ArmLeaseRenewalError):
            supervisor.checkpoint(measurement)
        event = supervisor.snapshot()["events"][-1]
        assert event["decision"] == "refused"
        assert "interlock" in event["reason"].lower()
    finally:
        external.deactivate()
        _close(instrument)


def _dynamic_runtime():
    instrument, _, _, _, _, supervisor, _ = _active_runtime()
    instrument.disable()
    instrument.arm(50)
    step = DynamicProfileStep(
        points=(ProfilePoint(0, 2), ProfilePoint(0.3, 3), ProfilePoint(0.6, 0)),
        kind="current", charge_voltage_limit_v=99, discharge_voltage_limit_v=80,
        current_limit_a=10, power_limit_w=500,
    )
    supervisor.begin_stage(index=0, name="dynamic", stage_type="csv_profile",
                           duration_s=1000, profile_step=step)
    return instrument, supervisor


def test_dynamic_profile_renews_after_approved_setpoint_change() -> None:
    instrument, supervisor = _dynamic_runtime()
    try:
        supervisor.apply_profile_point(index=0, value=2, mode=OperatingState.CHARGE)
        time.sleep(0.12)
        supervisor.apply_profile_point(index=1, value=3, mode=OperatingState.CHARGE)
        supervisor.checkpoint(instrument.read_measurement())
        assert supervisor.snapshot()["renewal_count"] == 1
        assert instrument.read_status().setpoints.current == 3
    finally:
        _close(instrument)


def test_dynamic_profile_rejects_skipped_point_and_external_change() -> None:
    instrument, supervisor = _dynamic_runtime()
    try:
        with pytest.raises(ArmLeaseRenewalError, match="out_of_order"):
            supervisor.apply_profile_point(index=1, value=3, mode=OperatingState.CHARGE)
        supervisor.apply_profile_point(index=0, value=2, mode=OperatingState.CHARGE)
        instrument.configure_setpoints(
            replace(instrument.read_status().setpoints, current=4)
        )
        time.sleep(0.12)
        with pytest.raises(ArmLeaseRenewalError, match="changed before approved profile point"):
            supervisor.apply_profile_point(index=1, value=3, mode=OperatingState.CHARGE)
    finally:
        _close(instrument)


def test_dynamic_profile_refuses_renewal_after_unapproved_change() -> None:
    instrument, supervisor = _dynamic_runtime()
    try:
        supervisor.apply_profile_point(index=0, value=2, mode=OperatingState.CHARGE)
        instrument.configure_setpoints(replace(instrument.read_status().setpoints, current=4))
        with pytest.raises(ArmLeaseRenewalError, match="setpoints changed"):
            supervisor.checkpoint(instrument.read_measurement())
        assert supervisor.snapshot()["renewal_count"] == 0
    finally:
        _close(instrument)


def test_renewal_stops_once_lease_covers_approved_stage_end() -> None:
    instrument, _, _, _, _, supervisor, measurement = _active_runtime()
    try:
        now = time.monotonic()
        supervisor._stage["deadline_monotonic"] = now + 10
        instrument._armed_until = now + 2
        supervisor.checkpoint(measurement)
        assert supervisor.snapshot()["renewal_count"] == 1
        assert instrument.read_status().armed_until_monotonic >= supervisor._stage["deadline_monotonic"]
        supervisor.checkpoint(instrument.read_measurement())
        assert supervisor.snapshot()["renewal_count"] == 1
    finally:
        supervisor.close("test_complete")
        _close(instrument)


def test_rest_duration_begins_after_disabled_setup(tmp_path, monkeypatch) -> None:
    instrument = NHR9300(
        "rest-sim", SimulatedBackend("rest-sim", initial_voltage_v=90),
        interlocks=[StaticInterlockProvider()],
    )
    instrument.connect()
    collector = AcquisitionCollector(instrument, rate_hz=10, csv_path=tmp_path / "rest.csv")
    original_disable = instrument.disable

    def slow_disable() -> None:
        time.sleep(0.25)
        original_disable()

    monkeypatch.setattr(instrument, "disable", slow_disable)
    supervisor = ArmLeaseSupervisor(
        run_id="rest-run", workflow_id="rest-workflow", bundle_digest="sim",
        instrument=instrument, collector=collector, safety_limits=_limits(),
        max_sequence_duration_s=2, stop_event=threading.Event(),
    )
    supervisor.start_sequence()
    try:
        result = SequenceRunner(
            instrument, collector, arm_lease_supervisor=supervisor,
        ).run((SequenceStage("rest", rest_period(name="rest", duration_s=0.3),
                             type="rest", duration_s=0.3),))
        assert result.state == RoutineState.PASSED, result.reason
        decisions = [event["decision"] for event in supervisor.snapshot()["events"]]
        assert decisions == ["rest_wait_started", "rest_wait_completed"]
    finally:
        supervisor.close("test_complete")
        instrument.close()
