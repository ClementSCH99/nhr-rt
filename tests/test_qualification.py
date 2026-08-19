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
from nhr9300.qualification import (
    LowSetpointValidator,
    LowSetpointProfile,
    WatchdogLossProfile,
    SafetyPrimitiveValidator,
    WatchdogLossValidator,
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
        profile_name="qualification-test",
    )


def test_safety_primitives_finish_disabled_without_enabling_output() -> None:
    backend = SimulatedBackend("sim")
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        results = SafetyPrimitiveValidator(instrument).run_safety_primitives(
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


def test_safety_primitives_refuse_an_initially_enabled_module() -> None:
    backend = SimulatedBackend("sim")
    backend.enabled = True
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        with pytest.raises(NHRStateError, match="Enabled=False"):
            SafetyPrimitiveValidator(instrument).run_safety_primitives(approved_limits())

    assert backend.enabled is True


def test_safety_primitives_refuse_an_active_initial_state() -> None:
    backend = SimulatedBackend("sim")
    backend.setpoints = Setpoints(state=OperatingState.DISCHARGE)
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        with pytest.raises(NHRStateError, match="OFF or STANDBY"):
            SafetyPrimitiveValidator(instrument).run_safety_primitives(approved_limits())


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


def low_setpoint_profile(**changes: object) -> LowSetpointProfile:
    values = {
        "mode": OperatingState.DISCHARGE,
        "current_a": 0.5,
        "voltage_v": 300.0,
        "power_w": 100.0,
        "duration_s": 0.1,
        "arm_duration_s": 5.0,
        "approved": True,
        "profile_name": "low-setpoint-test",
    }
    values.update(changes)
    return LowSetpointProfile(**values)


def test_low_setpoint_treats_setstate_as_activation_and_cleans_up() -> None:
    backend = SimulatedBackend("sim", initial_voltage_v=350.0)
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        result = LowSetpointValidator(instrument).run(
            low_setpoint_profile(), approved_limits()
        )

    assert result.active_status.enabled is True
    assert result.active_status.state == OperatingState.DISCHARGE
    assert result.active_measurements
    assert result.active_measurements[0].current_a == pytest.approx(-0.5)
    assert result.expected_current_delta_a == pytest.approx(-0.5)
    assert result.observed_current_delta_a == pytest.approx(-0.5)
    assert result.final_status.enabled is False
    assert result.final_status.setpoints.current_enabled is False
    assert result.final_status.setpoints.voltage_enabled is False
    assert result.final_status.setpoints.power_enabled is False
    assert instrument.may_be_energized is False


def test_low_setpoint_never_calls_direct_enable() -> None:
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
            low_setpoint_profile(), approved_limits()
        )

    assert result.final_status.enabled is False


def test_low_setpoint_interlock_failure_disables_output() -> None:
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
                low_setpoint_profile(), approved_limits()
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
def test_low_setpoint_rejects_profiles_outside_its_narrow_scope(
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
                low_setpoint_profile(**changes), approved_limits()
            )


def test_low_setpoint_voltage_precheck_cleans_up_and_clears_arm() -> None:
    backend = SimulatedBackend("sim", initial_voltage_v=500.0)
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        with pytest.raises(NHRStateError, match="outside"):
            LowSetpointValidator(instrument).run(
                low_setpoint_profile(), approved_limits()
            )
        final_status = instrument.read_status()

    assert final_status.enabled is False
    assert final_status.armed_until_monotonic is None


def watchdog_loss_profile(**changes: object) -> WatchdogLossProfile:
    values = {
        "mode": OperatingState.DISCHARGE,
        "current_a": 0.5,
        "voltage_v": 300.0,
        "power_w": 100.0,
        "pre_disconnect_duration_s": 0.05,
        "disconnect_duration_s": 0.25,
        "arm_duration_s": 5.0,
        "approved": True,
        "profile_name": "watchdog-loss-test",
    }
    values.update(changes)
    return WatchdogLossProfile(**values)


def test_watchdog_loss_observes_trip_and_restores_safe_state() -> None:
    backend = SimulatedBackend(
        "sim", initial_voltage_v=350.0, watchdog_timeout_s=0.05
    )
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        result = WatchdogLossValidator(instrument).run(
            watchdog_loss_profile(), approved_limits()
        )

    assert result.watchdog_before is False
    assert result.watchdog_enabled_readback is True
    assert result.active_status.enabled is True
    assert result.communication_error.startswith("NHRConnectionError:")
    assert result.status_after_reconnect.enabled is False
    assert result.status_after_reconnect.state == OperatingState.STANDBY
    assert result.watchdog_disabled_readback is False
    assert result.final_status.enabled is False
    assert backend.watchdog_enabled is False


def test_watchdog_loss_active_evidence_exists_before_connection_loss() -> None:
    backend = SimulatedBackend("sim", initial_voltage_v=350.0)
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        validator = WatchdogLossValidator(instrument)
        active = validator.prepare_active(watchdog_loss_profile(), approved_limits())
        assert active.watchdog_enabled_readback is True
        assert active.active_status.enabled is True
        assert active.observed_current_delta_a == pytest.approx(-0.5)
        validator.restore_safe_state(disable_watchdog=True)

    assert backend.enabled is False
    assert backend.watchdog_enabled is False


def test_watchdog_loss_fails_if_output_is_still_active_after_reconnect() -> None:
    backend = SimulatedBackend(
        "sim", initial_voltage_v=350.0, watchdog_timeout_s=60.0
    )
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        with pytest.raises(NHRStateError, match="Expected OFF or STANDBY"):
            WatchdogLossValidator(instrument).run(
                watchdog_loss_profile(), approved_limits()
            )
        final_status = instrument.read_status()

    assert final_status.enabled is False
    assert final_status.setpoints.current_enabled is False
    assert backend.watchdog_enabled is False


def test_watchdog_loss_rejects_long_communication_gap() -> None:
    with pytest.raises(NHRValidationError, match="between 0.25 and 10 s"):
        WatchdogLossValidator.validate_profile(
            watchdog_loss_profile(disconnect_duration_s=10.1), approved_limits()
        )


def test_watchdog_loss_does_not_disable_a_preexisting_watchdog() -> None:
    backend = SimulatedBackend("sim", initial_voltage_v=350.0)
    backend.watchdog_enabled = True
    instrument = NHR9300(
        "sim", backend, interlocks=[StaticInterlockProvider(safe=True)]
    )

    with instrument:
        with pytest.raises(NHRStateError, match="disabled initially"):
            WatchdogLossValidator(instrument).run(
                watchdog_loss_profile(), approved_limits()
            )

    assert backend.watchdog_enabled is True
