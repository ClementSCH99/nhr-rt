from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

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
from nhr9300.types import OperatingState, SafetyLimits, Setpoints


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
