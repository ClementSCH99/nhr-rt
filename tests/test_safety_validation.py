from __future__ import annotations

from dataclasses import replace
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
from nhr9300.errors import NHRStateError, NHRValidationError
from nhr9300.types import InterlockSignal
from nhr9300.safety_validation import (
    LowSetpointValidator,
    PhaseBProfile,
    SafetyPrimitiveValidator,
    require_disabled_inactive,
    safety_limit_mismatches,
)


def approved_limits() -> SafetyLimits:
    return SafetyLimits(
        charge_current=1.0,
        charge_voltage_max=420.0,
        charge_power=500.0,
        discharge_current=1.0,
        discharge_voltage_min=280.0,
        discharge_power=500.0,
        approved=True,
        profile_name="session3-test",
    )


def test_phase_a_finishes_disabled_without_enabling_output() -> None:
    backend = SimulatedBackend("sim")
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        results = SafetyPrimitiveValidator(instrument).run_phase_a(
            approved_limits()
        )
        status = instrument.read_status()
        limits_readback = instrument.read_safety_limits()

    instrument.connect()
    reconnected = instrument.read_status()
    instrument.close()

    assert [result.name for result in results] == [
        "disable",
        "configure_limits",
        "set_standby_channels_disabled",
        "standby",
        "final_disable",
    ]
    assert status.state == OperatingState.STANDBY
    assert status.enabled is False
    assert reconnected.state == OperatingState.STANDBY
    assert reconnected.enabled is False
    assert backend.limits == approved_limits()
    assert safety_limit_mismatches(
        approved_limits(), limits_readback
    ) == []


def test_phase_a_refuses_an_initially_enabled_module() -> None:
    backend = SimulatedBackend("sim")
    backend.enabled = True
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        with pytest.raises(NHRStateError, match="Enabled=False"):
            SafetyPrimitiveValidator(instrument).run_phase_a(approved_limits())

    assert backend.enabled is True


def test_phase_a_refuses_an_active_initial_state() -> None:
    backend = SimulatedBackend("sim")
    backend.setpoints = Setpoints(state=OperatingState.DISCHARGE)
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        with pytest.raises(NHRStateError, match="OFF or STANDBY"):
            SafetyPrimitiveValidator(instrument).run_phase_a(approved_limits())


def test_disabled_inactive_accepts_real_hardware_off_state() -> None:
    backend = SimulatedBackend("sim")
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        status = instrument.read_status()

    require_disabled_inactive(status)


def test_limit_verification_reports_a_readback_difference() -> None:
    backend = SimulatedBackend("sim")
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        instrument.configure_safety_limits(approved_limits())
        readback = instrument.read_safety_limits()

    changed = replace(readback, discharge_power=99.0)
    assert safety_limit_mismatches(approved_limits(), changed) == [
        "discharge_power: requested=500.0, readback=99.0"
    ]


def phase_b_profile(**changes: object) -> PhaseBProfile:
    values = {
        "mode": OperatingState.DISCHARGE,
        "current_a": 0.5,
        "voltage_v": 300.0,
        "power_w": 100.0,
        "duration_s": 0.1,
        "arm_duration_s": 5.0,
        "approved": True,
        "profile_name": "session3b-test",
    }
    values.update(changes)
    return PhaseBProfile(**values)


def test_phase_b_treats_setstate_as_activation_and_cleans_up() -> None:
    backend = SimulatedBackend("sim", initial_voltage_v=350.0)
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        result = LowSetpointValidator(instrument).run(
            phase_b_profile(), approved_limits()
        )

    assert result.active_status.enabled is True
    assert result.active_status.state == OperatingState.DISCHARGE
    assert result.active_measurements
    assert result.active_measurements[0].current_a == pytest.approx(0.5)
    assert result.final_status.enabled is False
    assert instrument.may_be_energized is False


def test_phase_b_never_calls_direct_enable() -> None:
    class RejectDirectEnableBackend(SimulatedBackend):
        def set_enabled(self, enabled: bool) -> None:
            if enabled:
                raise AssertionError("direct enable must not be called")
            super().set_enabled(enabled)

    instrument = NHR9300(
        "sim",
        RejectDirectEnableBackend("sim", initial_voltage_v=350.0),
        interlocks=[StaticInterlockProvider(safe=True)],
    )

    with instrument:
        result = LowSetpointValidator(instrument).run(
            phase_b_profile(), approved_limits()
        )

    assert result.final_status.enabled is False


def test_phase_b_interlock_failure_disables_output() -> None:
    class FailingRuntimeInterlock:
        def __init__(self) -> None:
            self.calls = 0

        def signals(self) -> list[InterlockSignal]:
            self.calls += 1
            return [
                InterlockSignal(
                    name="runtime-test",
                    safe=self.calls < 3,
                    timestamp_monotonic=time.monotonic(),
                )
            ]

    backend = SimulatedBackend("sim", initial_voltage_v=350.0)
    instrument = NHR9300(
        "sim", backend, interlocks=[FailingRuntimeInterlock()]
    )

    with instrument:
        with pytest.raises(Exception, match="runtime-test"):
            LowSetpointValidator(instrument).run(
                phase_b_profile(), approved_limits()
            )
        final_status = instrument.read_status()

    assert final_status.enabled is False
    assert instrument.may_be_energized is False


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"approved": False}, "approved"),
        ({"current_a": 1.1}, "<= 1 A"),
        ({"power_w": 101.0}, "<= 100 W"),
        ({"duration_s": 2.1}, "<= 2 s"),
        ({"arm_duration_s": 2.0}, "2 s of margin"),
    ],
)
def test_phase_b_rejects_profiles_outside_its_narrow_scope(
    changes: dict[str, object],
    message: str,
) -> None:
    instrument = NHR9300(
        "sim",
        SimulatedBackend("sim"),
        interlocks=[StaticInterlockProvider(safe=True)],
    )

    with instrument:
        with pytest.raises(NHRValidationError, match=message):
            LowSetpointValidator(instrument).run(
                phase_b_profile(**changes), approved_limits()
            )


def test_phase_b_voltage_precheck_cleans_up_and_clears_arm() -> None:
    backend = SimulatedBackend("sim", initial_voltage_v=500.0)
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        with pytest.raises(NHRStateError, match="outside"):
            LowSetpointValidator(instrument).run(
                phase_b_profile(), approved_limits()
            )
        final_status = instrument.read_status()

    assert final_status.enabled is False
    assert final_status.armed_until_monotonic is None
