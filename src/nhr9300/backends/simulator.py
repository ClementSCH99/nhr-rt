"""Deterministic in-memory NHR9300 simulator."""

from __future__ import annotations

import time

from ..errors import NHRConnectionError, NHRStateError
from ..types import (
    Capabilities,
    Identity,
    InstrumentStatus,
    Measurement,
    OperatingState,
    SafetyLimits,
    SafetyLimitsReadback,
    Setpoints,
)


class SimulatedBackend:
    """Deterministic software backend with an explicitly resettable state."""
    def __init__(
        self,
        instrument_id: str,
        *,
        initial_voltage_v: float = 350.0,
        capabilities: Capabilities | None = None,
        watchdog_timeout_s: float = 0.25,
    ) -> None:
        self.instrument_id = instrument_id
        self.connected = False
        self.enabled = False
        self.remote = True
        self.watchdog_enabled = False
        self.watchdog_timeout_s = watchdog_timeout_s
        self.initial_voltage_v = initial_voltage_v
        self.capabilities = capabilities or Capabilities(
            voltage_min=0.0,
            voltage_max=1000.0,
            charge_current_max=200.0,
            discharge_current_max=200.0,
            charge_power_max=100_000.0,
            discharge_power_max=100_000.0,
            current_slew_rate_min=0.1,
            current_slew_rate_max=1000.0,
            voltage_slew_rate_min=0.1,
            voltage_slew_rate_max=1000.0,
        )
        self.limits: SafetyLimits | None = None
        self.setpoints = Setpoints()
        self.failure: Exception | None = None
        self._voltage_v = initial_voltage_v
        self._last_update = time.monotonic()
        self._capacity_charge_ah = 0.0
        self._capacity_discharge_ah = 0.0
        self._energy_charge_kwh = 0.0
        self._energy_discharge_kwh = 0.0
        self._disconnected_at: float | None = None

    def reset_initial_state(self, initial_voltage_v: float) -> None:
        """Reset voltage and accumulated counters before a simulated workflow.

        The reset is allowed only while disconnected so a caller cannot rewrite
        the state of an active simulated run.
        """
        if self.connected:
            raise NHRStateError("Simulator initial state can only reset while disconnected")
        if not 0.0 <= initial_voltage_v <= self.capabilities.voltage_max:
            raise NHRStateError("Simulator initial voltage is outside capabilities")
        self.initial_voltage_v = initial_voltage_v
        self._voltage_v = initial_voltage_v
        self.enabled = False
        self.watchdog_enabled = False
        self.setpoints = Setpoints()
        self._capacity_charge_ah = 0.0
        self._capacity_discharge_ah = 0.0
        self._energy_charge_kwh = 0.0
        self._energy_discharge_kwh = 0.0
        self._last_update = time.monotonic()
        self._disconnected_at = None

    def _active_current(self) -> float:
        if self.enabled and self.setpoints.state in (
            OperatingState.CHARGE,
            OperatingState.DISCHARGE,
        ):
            sign = 1.0 if self.setpoints.state == OperatingState.CHARGE else -1.0
            candidates: list[float] = []
            if self.setpoints.control_mode == "power":
                if self.setpoints.power_enabled and self._voltage_v > 0.0:
                    candidates.append(self.setpoints.power / self._voltage_v)
                if self.setpoints.current_enabled:
                    candidates.append(self.setpoints.current)
            elif self.setpoints.current_enabled:
                candidates.append(self.setpoints.current)
            if not candidates:
                return 0.0
            magnitude = min(candidates)

            # The real NHR applies all enabled regulation channels together.
            # This small deterministic taper models the CC-to-CV transition and
            # the equivalent discharge voltage floor without pretending to be a
            # battery model.
            if self.setpoints.voltage_enabled:
                if self.setpoints.state == OperatingState.CHARGE:
                    headroom = self.setpoints.voltage - self._voltage_v
                else:
                    headroom = self._voltage_v - self.setpoints.voltage
                magnitude = min(magnitude, max(0.0, headroom * 50.0))
            return sign * magnitude
        return 0.0

    def _integrate(self) -> None:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_update)
        current = self._active_current()
        power = self._voltage_v * current
        self._voltage_v += current * 0.005 * elapsed
        self._capacity_charge_ah += max(current, 0.0) * elapsed / 3_600.0
        self._capacity_discharge_ah += min(current, 0.0) * elapsed / 3_600.0
        self._energy_charge_kwh += max(power, 0.0) * elapsed / 3_600_000.0
        self._energy_discharge_kwh += min(power, 0.0) * elapsed / 3_600_000.0
        self._last_update = now

    def _reset_counter_for_state(self, state: OperatingState) -> None:
        if state == OperatingState.CHARGE and self.setpoints.state != state:
            self._capacity_charge_ah = 0.0
            self._energy_charge_kwh = 0.0
        elif state == OperatingState.DISCHARGE and self.setpoints.state != state:
            self._capacity_discharge_ah = 0.0
            self._energy_discharge_kwh = 0.0

    def _check(self) -> None:
        if self.failure is not None:
            raise self.failure
        if not self.connected:
            raise NHRConnectionError(f"{self.instrument_id} is not connected")

    def connect(self) -> None:
        if (
            self.watchdog_enabled
            and self._disconnected_at is not None
            and time.monotonic() - self._disconnected_at >= self.watchdog_timeout_s
        ):
            self.enabled = False
            self.setpoints = Setpoints(state=OperatingState.STANDBY)
        self.connected = True
        self._disconnected_at = None
        self._last_update = time.monotonic()

    def close(self) -> None:
        self._integrate()
        self._disconnected_at = time.monotonic()
        self.connected = False

    def read_identity(self) -> Identity:
        self._check()
        return Identity(self.instrument_id, "SIM-9300", "SIMULATOR", "1.0", "N/A")

    def read_capabilities(self) -> Capabilities:
        self._check()
        return self.capabilities

    def read_status(self) -> InstrumentStatus:
        self._check()
        return InstrumentStatus(
            instrument_id=self.instrument_id,
            connected=True,
            remote=self.remote,
            enabled=self.enabled,
            state=self.setpoints.state,
            setpoints=self.setpoints,
        )

    def read_measurement(self) -> Measurement:
        self._check()
        self._integrate()
        current = self._active_current()
        power = self._voltage_v * current
        return Measurement.now(
            self.instrument_id,
            time.monotonic(),
            self._voltage_v,
            current,
            power,
            capacity_charge_ah=self._capacity_charge_ah,
            capacity_discharge_ah=self._capacity_discharge_ah,
            energy_charge_kwh=self._energy_charge_kwh,
            energy_discharge_kwh=self._energy_discharge_kwh,
            temperature_c=25.0 + abs(current) * 0.002,
        )

    def configure_safety_limits(self, limits: SafetyLimits) -> None:
        self._check()
        self.limits = limits

    def read_safety_limits(self) -> SafetyLimitsReadback:
        self._check()
        if self.limits is None:
            raise NHRStateError("Safety limits have not been configured")
        limits = self.limits
        return SafetyLimitsReadback(
            charge_current=limits.charge_current,
            charge_current_delay_s=limits.current_delay_s,
            charge_voltage_max=limits.charge_voltage_max,
            charge_voltage_delay_s=limits.voltage_delay_s,
            charge_power=limits.charge_power,
            charge_power_delay_s=limits.power_delay_s,
            discharge_current=limits.discharge_current,
            discharge_current_delay_s=limits.current_delay_s,
            discharge_voltage_min=limits.discharge_voltage_min,
            discharge_voltage_delay_s=limits.voltage_delay_s,
            discharge_power=limits.discharge_power,
            discharge_power_delay_s=limits.power_delay_s,
            uut_temperature_max=limits.uut_temperature_max,
        )

    def configure_setpoints(self, setpoints: Setpoints) -> None:
        self._check()
        self._integrate()
        self._reset_counter_for_state(setpoints.state)
        self.setpoints = setpoints
        # Real NHR hardware enables its input when SetState selects a mode.
        self.enabled = setpoints.state != OperatingState.OFF

    def set_enabled(self, enabled: bool) -> None:
        self._check()
        self._integrate()
        self.enabled = enabled

    def set_state(self, state: OperatingState) -> None:
        self._check()
        self._integrate()
        self._reset_counter_for_state(state)
        self.enabled = state != OperatingState.OFF
        self.setpoints = Setpoints(
            state=state,
            voltage=self.setpoints.voltage,
            current=self.setpoints.current,
            power=self.setpoints.power,
            resistance=self.setpoints.resistance,
            voltage_enabled=self.setpoints.voltage_enabled,
            current_enabled=self.setpoints.current_enabled,
            power_enabled=self.setpoints.power_enabled,
            resistance_enabled=self.setpoints.resistance_enabled,
            voltage_slew_rate=self.setpoints.voltage_slew_rate,
            current_slew_rate=self.setpoints.current_slew_rate,
            power_slew_rate=self.setpoints.power_slew_rate,
            resistance_slew_rate=self.setpoints.resistance_slew_rate,
            control_mode=self.setpoints.control_mode,
        )

    def set_watchdog(self, enabled: bool) -> None:
        self._check()
        self.watchdog_enabled = enabled

    def read_watchdog(self) -> bool:
        self._check()
        return self.watchdog_enabled
