"""Deterministic in-memory NHR9300 simulator."""

from __future__ import annotations

import time

from ..errors import NHRConnectionError
from ..types import (
    Capabilities,
    Identity,
    InstrumentStatus,
    Measurement,
    OperatingState,
    SafetyLimits,
    Setpoints,
)


class SimulatedBackend:
    def __init__(
        self,
        instrument_id: str,
        *,
        initial_voltage_v: float = 350.0,
        capabilities: Capabilities | None = None,
    ) -> None:
        self.instrument_id = instrument_id
        self.connected = False
        self.enabled = False
        self.remote = True
        self.watchdog_enabled = False
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
        self._started = time.monotonic()

    def _check(self) -> None:
        if self.failure is not None:
            raise self.failure
        if not self.connected:
            raise NHRConnectionError(f"{self.instrument_id} is not connected")

    def connect(self) -> None:
        self.connected = True
        self._started = time.monotonic()

    def close(self) -> None:
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
        current = 0.0
        if self.enabled and self.setpoints.state in (
            OperatingState.CHARGE,
            OperatingState.DISCHARGE,
        ):
            sign = -1.0 if self.setpoints.state == OperatingState.CHARGE else 1.0
            current = sign * self.setpoints.current
        elapsed = time.monotonic() - self._started
        voltage = self.initial_voltage_v + (-current * 0.005 * min(elapsed, 10.0))
        power = voltage * current
        return Measurement.now(
            self.instrument_id,
            time.monotonic(),
            voltage,
            current,
            power,
            energy_charge_kwh=max(-power, 0.0) * elapsed / 3_600_000.0,
            energy_discharge_kwh=max(power, 0.0) * elapsed / 3_600_000.0,
            temperature_c=25.0 + abs(current) * 0.002,
        )

    def configure_safety_limits(self, limits: SafetyLimits) -> None:
        self._check()
        self.limits = limits

    def configure_setpoints(self, setpoints: Setpoints) -> None:
        self._check()
        self.setpoints = setpoints

    def set_enabled(self, enabled: bool) -> None:
        self._check()
        self.enabled = enabled

    def set_state(self, state: OperatingState) -> None:
        self._check()
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
        )

    def set_watchdog(self, enabled: bool) -> None:
        self._check()
        self.watchdog_enabled = enabled
