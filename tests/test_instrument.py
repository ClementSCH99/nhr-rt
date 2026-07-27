from __future__ import annotations

import time

import pytest

from nhr9300 import (
    NHR9300,
    OperatingState,
    SafetyLimits,
    Setpoints,
    SimulatedBackend,
    StaticInterlockProvider,
)
from nhr9300.errors import (
    NHRInterlockError,
    NHRNotArmedError,
    NHRValidationError,
)


def approved_limits(**changes: object) -> SafetyLimits:
    values = {
        "charge_current": 10.0,
        "charge_voltage_max": 420.0,
        "charge_power": 5000.0,
        "discharge_current": 10.0,
        "discharge_voltage_min": 280.0,
        "discharge_power": 5000.0,
        "approved": True,
        "profile_name": "test-profile",
    }
    values.update(changes)
    return SafetyLimits(**values)


def make_instrument(
    interlock: StaticInterlockProvider | None = None,
) -> tuple[NHR9300, SimulatedBackend]:
    backend = SimulatedBackend("sim")
    instrument = NHR9300(
        "sim",
        backend,
        interlocks=[interlock or StaticInterlockProvider()],
    )
    return instrument, backend


def test_connect_is_non_destructive() -> None:
    instrument, backend = make_instrument()
    backend.enabled = True
    backend.setpoints = Setpoints(state=OperatingState.STANDBY)
    instrument.connect()
    assert instrument.read_status().enabled is True
    instrument.close()
    assert backend.enabled is True


def test_arm_refuses_observed_enabled_module() -> None:
    instrument, backend = make_instrument()
    backend.enabled = True
    instrument.connect()
    instrument.configure_safety_limits(approved_limits())
    with pytest.raises(Exception, match="enabled"):
        instrument.arm()
    instrument.disable()
    instrument.arm()
    instrument.close()


def test_enable_requires_limits_arm_and_fresh_measurement() -> None:
    instrument, backend = make_instrument()
    with instrument:
        with pytest.raises(NHRValidationError):
            instrument.configure_safety_limits(
                approved_limits(approved=False, profile_name="draft")
            )
        instrument.configure_safety_limits(approved_limits())
        setpoints = Setpoints(
            state=OperatingState.DISCHARGE,
            voltage=300.0,
            current=1.0,
            power=500.0,
            voltage_enabled=True,
            current_enabled=True,
            power_enabled=True,
        )
        with pytest.raises(NHRNotArmedError):
            instrument.configure_setpoints(setpoints)
        instrument.arm()
        instrument.configure_setpoints(setpoints)
        with pytest.raises(Exception, match="fresh measurement"):
            instrument.enable()
        instrument.read_measurement()
        instrument.enable()
        assert backend.enabled
        instrument.disable()
        assert not backend.enabled


def test_rejects_setpoints_above_approved_limits() -> None:
    instrument, _ = make_instrument()
    with instrument:
        instrument.configure_safety_limits(approved_limits(discharge_current=2.0))
        instrument.arm()
        with pytest.raises(NHRValidationError, match="current"):
            instrument.configure_setpoints(
                Setpoints(
                    state=OperatingState.DISCHARGE,
                    voltage=300.0,
                    current=3.0,
                    power=100.0,
                    current_enabled=True,
                )
            )


def test_interlock_blocks_arm_and_runtime() -> None:
    interlock = StaticInterlockProvider()
    instrument, _ = make_instrument(interlock)
    with instrument:
        instrument.configure_safety_limits(approved_limits())
        interlock.safe = False
        with pytest.raises(NHRInterlockError):
            instrument.arm()


def test_arm_expiry_is_enforced() -> None:
    instrument, _ = make_instrument()
    with instrument:
        instrument.configure_safety_limits(approved_limits())
        instrument.arm(1.0)
        time.sleep(1.05)
        with pytest.raises(NHRNotArmedError):
            instrument.enable()
