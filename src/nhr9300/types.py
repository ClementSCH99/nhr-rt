"""Public data types shared by drivers, routines, logging and the service."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any


class OperatingState(IntEnum):
    OFF = 0
    STANDBY = 1
    CHARGE = 2
    DISCHARGE = 3
    BATTERY_EMULATION = 4


class RoutineState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Identity:
    logical_name: str
    serial_number: str
    part_number: str
    hardware_revision: str = ""
    calibration_date: str = ""


@dataclass(frozen=True, slots=True)
class Capabilities:
    voltage_min: float
    voltage_max: float
    charge_current_max: float
    discharge_current_max: float
    charge_power_max: float
    discharge_power_max: float
    current_slew_rate_min: float = 0.0
    current_slew_rate_max: float = float("inf")
    voltage_slew_rate_min: float = 0.0
    voltage_slew_rate_max: float = float("inf")


@dataclass(frozen=True, slots=True)
class SafetyLimits:
    charge_current: float
    charge_voltage_max: float
    charge_power: float
    discharge_current: float
    discharge_voltage_min: float
    discharge_power: float
    current_delay_s: float = 0.1
    voltage_delay_s: float = 0.1
    power_delay_s: float = 0.1
    uut_temperature_max: float | None = None
    approved: bool = False
    profile_name: str = ""


@dataclass(frozen=True, slots=True)
class SafetyLimitsReadback:
    """Safety limits as they are read back from the NHR hardware."""

    charge_current: float
    charge_current_delay_s: float
    charge_voltage_max: float
    charge_voltage_delay_s: float
    charge_power: float
    charge_power_delay_s: float
    discharge_current: float
    discharge_current_delay_s: float
    discharge_voltage_min: float
    discharge_voltage_delay_s: float
    discharge_power: float
    discharge_power_delay_s: float
    uut_temperature_max: float | None = None


@dataclass(frozen=True, slots=True)
class Setpoints:
    state: OperatingState = OperatingState.STANDBY
    voltage: float = 0.0
    current: float = 0.0
    power: float = 0.0
    resistance: float = 0.0
    voltage_enabled: bool = False
    current_enabled: bool = False
    power_enabled: bool = False
    resistance_enabled: bool = False
    voltage_slew_rate: float | None = None
    current_slew_rate: float | None = None
    power_slew_rate: float | None = None
    resistance_slew_rate: float | None = None


@dataclass(frozen=True, slots=True)
class InstrumentStatus:
    instrument_id: str
    connected: bool
    remote: bool
    enabled: bool
    state: OperatingState
    setpoints: Setpoints
    armed_until_monotonic: float | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class Measurement:
    instrument_id: str
    timestamp_utc: datetime
    monotonic_s: float
    voltage_v: float
    current_a: float
    power_w: float
    capacity_charge_ah: float | None = None
    capacity_discharge_ah: float | None = None
    energy_charge_kwh: float | None = None
    energy_discharge_kwh: float | None = None
    temperature_c: float | None = None

    @classmethod
    def now(
        cls,
        instrument_id: str,
        monotonic_s: float,
        voltage_v: float,
        current_a: float,
        power_w: float,
        **kwargs: Any,
    ) -> Measurement:
        return cls(
            instrument_id=instrument_id,
            timestamp_utc=datetime.now(timezone.utc),
            monotonic_s=monotonic_s,
            voltage_v=voltage_v,
            current_a=current_a,
            power_w=power_w,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class InterlockSignal:
    name: str
    safe: bool
    timestamp_monotonic: float
    detail: str = ""


@dataclass(slots=True)
class RoutineEvent:
    timestamp_utc: datetime
    step: str
    message: str


@dataclass(slots=True)
class RoutineResult:
    routine_id: str
    state: RoutineState = RoutineState.PENDING
    reason: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    csv_path: str | None = None
    termination_reason: str | None = None
    termination_field: str | None = None
    termination_value: float | None = None
    termination_baseline: float | None = None
    termination_measurement: Measurement | None = None
    events: list[RoutineEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def to_jsonable(value: Any) -> Any:
    """Convert public dataclasses/enums/datetimes into JSON-compatible values."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return value
