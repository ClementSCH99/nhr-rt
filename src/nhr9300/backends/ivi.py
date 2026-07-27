"""Windows IVI-COM backend for the official NH Research driver."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..errors import NHRConnectionError, NHRDriverError
from ..types import (
    Capabilities,
    Identity,
    InstrumentStatus,
    Measurement,
    OperatingState,
    SafetyLimits,
    Setpoints,
)

DEFAULT_DRIVER_DLL = Path(
    r"C:\Program Files (x86)\IVI Foundation\IVI\Bin\NHRDCPowerModule.dll"
)


class IVIBackend:
    """Thin adapter. All methods must be called from one COM-initialized thread."""

    def __init__(
        self,
        instrument_id: str,
        resource_name: str,
        driver_dll: Path = DEFAULT_DRIVER_DLL,
    ) -> None:
        self.instrument_id = instrument_id
        self.resource_name = resource_name
        self.driver_dll = Path(driver_dll)
        self._driver: Any | None = None
        self._comtypes: Any | None = None

    @staticmethod
    def _wrap(action: str, function: Any) -> Any:
        try:
            return function()
        except Exception as exc:
            raise NHRDriverError(f"IVI-COM {action} failed: {exc}") from exc

    def connect(self) -> None:
        if not self.driver_dll.is_file():
            raise NHRConnectionError(f"NHR IVI driver not found: {self.driver_dll}")
        try:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()
            self._comtypes = comtypes
            module = comtypes.client.GetModule(str(self.driver_dll))
            driver = comtypes.client.CreateObject(
                module.NHRDCPowerModule,
                interface=module.INHRDCPowerModule,
            )
            driver.Initialize(self.resource_name, False, False, "")
            self._driver = driver
        except Exception as exc:
            if self._comtypes is not None:
                self._comtypes.CoUninitialize()
                self._comtypes = None
            raise NHRConnectionError(
                f"Could not initialize logical resource {self.resource_name!r}: {exc}"
            ) from exc

    def close(self) -> None:
        try:
            if self._driver is not None:
                self._driver.Close()
        finally:
            self._driver = None
            if self._comtypes is not None:
                self._comtypes.CoUninitialize()
                self._comtypes = None

    @property
    def driver(self) -> Any:
        if self._driver is None:
            raise NHRConnectionError(f"{self.instrument_id} is not connected")
        return self._driver

    def read_identity(self) -> Identity:
        def read() -> Identity:
            maintenance = self.driver.Maintenance
            return Identity(
                logical_name=str(self.driver.LogicalName),
                serial_number=str(maintenance.SerialNumber),
                part_number=str(maintenance.PartNumber),
                hardware_revision=str(maintenance.HardwareRevision),
                calibration_date=str(maintenance.CalibrationDate),
            )

        return self._wrap("read identity", read)

    def read_capabilities(self) -> Capabilities:
        def read() -> Capabilities:
            caps = self.driver.Input.Capabilities
            return Capabilities(
                voltage_min=float(caps.VoltageMin),
                voltage_max=float(caps.VoltageMax),
                charge_current_max=float(caps.ChargeCurrentMax),
                discharge_current_max=float(caps.DischargeCurrentMax),
                charge_power_max=float(caps.ChargePowerMax),
                discharge_power_max=float(caps.DischargePowerMax),
                current_slew_rate_min=float(caps.CurrentSlewRateMin),
                current_slew_rate_max=float(caps.CurrentSlewRateMax),
                voltage_slew_rate_min=float(caps.VoltageSlewRateMin),
                voltage_slew_rate_max=float(caps.VoltageSlewRateMax),
            )

        return self._wrap("read capabilities", read)

    def _setpoints(self) -> Setpoints:
        operation = self.driver.Input.Operation
        return Setpoints(
            state=OperatingState(int(operation.OperatingState)),
            voltage=float(operation.Voltage),
            current=float(operation.Current),
            power=float(operation.Power),
            resistance=float(operation.Resistance),
            voltage_enabled=bool(operation.VoltageEnabled),
            current_enabled=bool(operation.CurrentEnabled),
            power_enabled=bool(operation.PowerEnabled),
            resistance_enabled=bool(operation.ResistanceEnabled),
            voltage_slew_rate=float(operation.VoltageSlewRate),
            current_slew_rate=float(operation.CurrentSlewRate),
            power_slew_rate=float(operation.PowerSlewRate),
            resistance_slew_rate=float(operation.ResistanceSlewRate),
        )

    def read_status(self) -> InstrumentStatus:
        return self._wrap(
            "read status",
            lambda: InstrumentStatus(
                instrument_id=self.instrument_id,
                connected=True,
                remote=bool(self.driver.Remote.State),
                enabled=bool(self.driver.Input.Enabled),
                state=OperatingState(int(self.driver.Input.Operation.OperatingState)),
                setpoints=self._setpoints(),
            ),
        )

    def read_measurement(self) -> Measurement:
        def read() -> Measurement:
            module = self._comtypes.client.GetModule(str(self.driver_dll))
            measurement = self.driver.Input.Measurement
            immediate = module.NHRDCPowerModuleMeasurementMethodImmediate
            voltage = float(measurement.Read(module.NHRDCPowerModuleMeasureVoltage, immediate))
            current = float(measurement.Read(module.NHRDCPowerModuleMeasureCurrent, immediate))
            power = float(measurement.Read(module.NHRDCPowerModuleMeasurePower, immediate))

            def optional(kind: int) -> float | None:
                try:
                    value = float(measurement.Read(kind, immediate))
                    return None if bool(measurement.IsInvalid(value)) else value
                except Exception:
                    return None

            return Measurement.now(
                self.instrument_id,
                time.monotonic(),
                voltage,
                current,
                power,
                energy_charge_kwh=optional(
                    module.NHRDCPowerModuleMeasureKiloWattHourCharge
                ),
                energy_discharge_kwh=optional(
                    module.NHRDCPowerModuleMeasureKiloWattHourDischarge
                ),
                temperature_c=optional(module.NHRDCPowerModuleMeasureUutTemperature),
            )

        return self._wrap("read measurement", read)

    def configure_safety_limits(self, limits: SafetyLimits) -> None:
        def write() -> None:
            safety = self.driver.Input.SafetyLimits
            safety.SetChargeLimits(
                limits.charge_current,
                limits.current_delay_s,
                limits.charge_voltage_max,
                limits.voltage_delay_s,
                limits.charge_power,
                limits.power_delay_s,
            )
            safety.SetDischargeLimits(
                limits.discharge_current,
                limits.current_delay_s,
                limits.discharge_voltage_min,
                limits.voltage_delay_s,
                limits.discharge_power,
                limits.power_delay_s,
            )
            if limits.uut_temperature_max is not None:
                safety.SetUutTemperatureLimits(limits.uut_temperature_max)

        self._wrap("configure safety limits", write)

    def configure_setpoints(self, setpoints: Setpoints) -> None:
        def write() -> None:
            operation = self.driver.Input.Operation
            if setpoints.voltage_slew_rate is not None:
                operation.VoltageSlewRate = setpoints.voltage_slew_rate
            if setpoints.current_slew_rate is not None:
                operation.CurrentSlewRate = setpoints.current_slew_rate
            if setpoints.power_slew_rate is not None:
                operation.PowerSlewRate = setpoints.power_slew_rate
            if setpoints.resistance_slew_rate is not None:
                operation.ResistanceSlewRate = setpoints.resistance_slew_rate
            operation.SetState(
                int(setpoints.state),
                setpoints.voltage_enabled,
                setpoints.voltage,
                setpoints.current_enabled,
                setpoints.current,
                setpoints.power_enabled,
                setpoints.power,
                setpoints.resistance_enabled,
                setpoints.resistance,
            )

        self._wrap("configure setpoints", write)

    def set_enabled(self, enabled: bool) -> None:
        self._wrap("set enabled", lambda: setattr(self.driver.Input, "Enabled", enabled))

    def set_state(self, state: OperatingState) -> None:
        current = self._setpoints()
        self.configure_setpoints(
            Setpoints(
                state=state,
                voltage=current.voltage,
                current=current.current,
                power=current.power,
                resistance=current.resistance,
                voltage_enabled=current.voltage_enabled,
                current_enabled=current.current_enabled,
                power_enabled=current.power_enabled,
                resistance_enabled=current.resistance_enabled,
                voltage_slew_rate=current.voltage_slew_rate,
                current_slew_rate=current.current_slew_rate,
                power_slew_rate=current.power_slew_rate,
                resistance_slew_rate=current.resistance_slew_rate,
            )
        )

    def set_watchdog(self, enabled: bool) -> None:
        self._wrap(
            "set watchdog",
            lambda: setattr(self.driver.Input.SafetyLimits, "Watchdog", enabled),
        )
