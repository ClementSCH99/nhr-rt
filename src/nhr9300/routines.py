"""Typed routine state machine and YAML loader."""

from __future__ import annotations

import threading
import time
import uuid
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .acquisition import AcquisitionCollector
from .errors import NHRRoutineError, NHRValidationError
from .instrument import NHR9300
from .types import (
    Measurement,
    OperatingState,
    RoutineEvent,
    RoutineResult,
    RoutineState,
    SafetyLimits,
    Setpoints,
)


@dataclass(frozen=True, slots=True)
class Condition:
    field: str
    operator: str
    value: float
    relative: bool = False

    def measurement_value(
        self,
        measurement: Measurement,
        mode: OperatingState | None = None,
    ) -> float | None:
        fields = {
            "voltage": measurement.voltage_v,
            "current": measurement.current_a,
            "current_magnitude": abs(measurement.current_a),
            "cutoff_current": abs(measurement.current_a),
            "power": measurement.power_w,
            "power_magnitude": abs(measurement.power_w),
            "temperature": measurement.temperature_c,
            "capacity_charge_ah": measurement.capacity_charge_ah,
            "capacity_discharge_ah": measurement.capacity_discharge_ah,
            "energy_charge_kwh": measurement.energy_charge_kwh,
            "energy_discharge_kwh": measurement.energy_discharge_kwh,
        }
        if self.field == "capacity_ah":
            if mode == OperatingState.CHARGE:
                value = measurement.capacity_charge_ah
                return None if value is None else abs(value)
            if mode == OperatingState.DISCHARGE:
                value = measurement.capacity_discharge_ah
                return None if value is None else abs(value)
            raise NHRValidationError("capacity_ah requires CHARGE or DISCHARGE mode")
        if self.field == "energy_wh":
            if mode == OperatingState.CHARGE:
                value = measurement.energy_charge_kwh
            elif mode == OperatingState.DISCHARGE:
                value = measurement.energy_discharge_kwh
            else:
                raise NHRValidationError("energy_wh requires CHARGE or DISCHARGE mode")
            return None if value is None else abs(value) * 1000.0
        if self.field not in fields:
            raise NHRValidationError(f"Unsupported condition field: {self.field}")
        return fields[self.field]

    def evaluate(
        self,
        measurement: Measurement,
        mode: OperatingState | None = None,
    ) -> bool:
        actual = self.measurement_value(measurement, mode)
        return False if actual is None else self.evaluate_value(actual)

    def evaluate_value(self, actual: float) -> bool:
        comparisons: dict[str, Callable[[float, float], bool]] = {
            "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b,
            ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b,
            "==": lambda a, b: a == b,
        }
        try:
            return comparisons[self.operator](actual, self.value)
        except KeyError as exc:
            raise NHRValidationError(
                f"Unsupported condition operator: {self.operator}"
            ) from exc


class Step:
    name = "step"

    def execute(self, context: RoutineContext) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ConfigureLimitsStep(Step):
    limits: SafetyLimits
    name = "configure_limits"

    def execute(self, context: RoutineContext) -> None:
        context.instrument.configure_safety_limits(self.limits)


@dataclass(frozen=True, slots=True)
class ArmStep(Step):
    duration_s: float = 30.0
    name = "arm"

    def execute(self, context: RoutineContext) -> None:
        context.instrument.arm(self.duration_s)


@dataclass(frozen=True, slots=True)
class MeasureStep(Step):
    name = "measure"

    def execute(self, context: RoutineContext) -> None:
        context.instrument.read_measurement()


@dataclass(frozen=True, slots=True)
class SetpointsStep(Step):
    setpoints: Setpoints
    name = "setpoints"

    def execute(self, context: RoutineContext) -> None:
        context.instrument.configure_setpoints(self.setpoints)


@dataclass(frozen=True, slots=True)
class EnableStep(Step):
    name = "enable"

    def execute(self, context: RoutineContext) -> None:
        context.instrument.enable()


@dataclass(frozen=True, slots=True)
class WaitStep(Step):
    duration_s: float
    condition: Condition | None = None
    poll_interval_s: float = 0.1
    mode: OperatingState | None = None
    activation_condition: Condition | None = None
    activation_tolerance: float = 0.0
    name = "wait"

    def execute(self, context: RoutineContext) -> None:
        if self.duration_s <= 0:
            raise NHRValidationError("Wait duration must be positive")
        if self.activation_tolerance < 0.0:
            raise NHRValidationError("Activation tolerance cannot be negative")
        deadline = time.monotonic() + self.duration_s
        baseline: float | None = None
        latest_measurement: Measurement | None = None
        latest_compared: float | None = None
        condition_active = self.activation_condition is None
        while True:
            if context.stop_event.is_set():
                raise InterruptedError("Routine stop requested")
            if context.collector.error:
                raise NHRRoutineError(context.collector.error)
            context.instrument.check_interlocks()
            measurement = context.instrument.read_measurement()
            latest_measurement = measurement
            if self.mode in (OperatingState.CHARGE, OperatingState.DISCHARGE):
                if context.result.initial_active_measurement is None:
                    context.result.initial_active_measurement = measurement
            if not condition_active and self.activation_condition is not None:
                activation_value = self.activation_condition.measurement_value(
                    measurement, self.mode
                )
                if activation_value is None:
                    raise NHRRoutineError(
                        "Termination activation field "
                        f"{self.activation_condition.field!r} is unavailable"
                    )
                condition_active = self.activation_condition.evaluate_value(
                    activation_value
                ) or math.isclose(
                    activation_value,
                    self.activation_condition.value,
                    rel_tol=0.0,
                    abs_tol=self.activation_tolerance,
                )
            if self.condition:
                actual = self.condition.measurement_value(measurement, self.mode)
                if actual is None:
                    raise NHRRoutineError(
                        f"Termination field {self.condition.field!r} is unavailable"
                    )
                if self.condition.relative:
                    if baseline is None:
                        baseline = actual
                    compared = actual - baseline
                else:
                    compared = actual
                latest_compared = compared
                if condition_active and self.condition.evaluate_value(compared):
                    context.result.termination_reason = "condition"
                    context.result.termination_field = self.condition.field
                    context.result.termination_value = compared
                    context.result.termination_baseline = baseline
                    context.result.termination_measurement = measurement
                    return
            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0.0:
                break
            context.stop_event.wait(min(self.poll_interval_s, remaining_s))
        if self.condition is not None:
            context.result.termination_reason = "condition_timeout"
            context.result.termination_field = self.condition.field
            context.result.termination_value = latest_compared
            context.result.termination_baseline = baseline
            context.result.termination_measurement = latest_measurement
            raise NHRRoutineError(
                f"Termination condition {self.condition.field!r} was not reached "
                f"within {self.duration_s:.3f} s"
            )
        context.result.termination_reason = "duration"
        context.result.termination_measurement = latest_measurement


@dataclass(frozen=True, slots=True)
class StandbyStep(Step):
    name = "standby"

    def execute(self, context: RoutineContext) -> None:
        context.instrument.standby()


@dataclass(frozen=True, slots=True)
class DisableStep(Step):
    name = "disable"

    def execute(self, context: RoutineContext) -> None:
        context.instrument.disable()


@dataclass(frozen=True, slots=True)
class Routine:
    name: str
    steps: Sequence[Step]


@dataclass(slots=True)
class RoutineContext:
    instrument: NHR9300
    collector: AcquisitionCollector
    stop_event: threading.Event
    result: RoutineResult


class RoutineRunner:
    def __init__(
        self,
        instrument: NHR9300,
        collector: AcquisitionCollector,
        *,
        manage_collector: bool = True,
    ) -> None:
        self.instrument = instrument
        self.collector = collector
        self.manage_collector = manage_collector
        self.result: RoutineResult | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, routine: Routine) -> RoutineResult:
        if self.running:
            raise NHRRoutineError("A routine is already running")
        if not self.manage_collector and not self.collector.running:
            raise NHRRoutineError("An externally managed collector must be running")
        result = RoutineResult(routine_id=str(uuid.uuid4()))
        self.result = result
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._execute,
            args=(routine, result),
            name=f"routine-{self.instrument.instrument_id}",
            daemon=True,
        )
        self._thread.start()
        return result

    def run(self, routine: Routine) -> RoutineResult:
        result = self.start(routine)
        try:
            self._thread.join()
        except KeyboardInterrupt:
            self.stop()
            self._thread.join(timeout=3.0)
            raise
        return result

    def stop(self) -> None:
        self._stop.set()
        try:
            self.instrument.emergency_stop("Routine stop requested")
        except Exception:
            pass

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the active routine and report whether it terminated."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _event(self, result: RoutineResult, step: str, message: str) -> None:
        result.events.append(RoutineEvent(datetime.now(timezone.utc), step, message))

    def _execute(self, routine: Routine, result: RoutineResult) -> None:
        result.state = RoutineState.RUNNING
        result.started_at = datetime.now(timezone.utc)
        if self.manage_collector:
            self.collector.start()
        result.csv_path = (
            str(self.collector.csv_path) if self.collector.csv_path is not None else None
        )
        context = RoutineContext(self.instrument, self.collector, self._stop, result)
        try:
            for index, step in enumerate(routine.steps):
                step_name = f"{index:02d}_{step.name}"
                self.collector.set_context(result.routine_id, step_name)
                self._event(result, step_name, "started")
                step.execute(context)
                self._event(result, step_name, "completed")
            result.state = RoutineState.PASSED
            if result.termination_reason == "condition":
                result.reason = (
                    f"Termination condition reached: {result.termination_field}="
                    f"{result.termination_value}"
                )
            elif result.termination_reason == "duration":
                result.reason = "Configured duration elapsed"
            else:
                result.reason = "Routine completed"
        except InterruptedError as exc:
            result.state = RoutineState.STOPPED
            result.reason = str(exc)
        except Exception as exc:
            result.state = RoutineState.FAILED
            result.reason = str(exc)
            result.errors.append(repr(exc))
        finally:
            if result.state != RoutineState.PASSED:
                try:
                    self.instrument.emergency_stop(
                        f"Routine {result.state.value}: {result.reason}"
                    )
                except Exception as exc:
                    result.errors.append(f"Emergency stop failed: {exc!r}")
            self.collector.set_context(result.routine_id, "finished")
            if self.manage_collector:
                self.collector.stop()
            result.ended_at = datetime.now(timezone.utc)


def constant_current_hold(
    *,
    name: str,
    limits: SafetyLimits,
    mode: OperatingState,
    current_a: float,
    voltage_v: float,
    power_w: float,
    duration_s: float,
    arm_duration_s: float = 30.0,
    termination: Condition | None = None,
    termination_activation: Condition | None = None,
    termination_activation_tolerance: float = 0.0,
    configure_limits: bool = True,
    voltage_limit_enabled: bool = True,
    power_limit_enabled: bool = True,
) -> Routine:
    if mode not in (OperatingState.CHARGE, OperatingState.DISCHARGE):
        raise NHRValidationError("CC hold mode must be CHARGE or DISCHARGE")
    steps: list[Step] = []
    if configure_limits:
        steps.append(ConfigureLimitsStep(limits))
    steps.extend(
        (
            ArmStep(arm_duration_s),
            MeasureStep(),
            SetpointsStep(
                Setpoints(
                    state=mode,
                    voltage=voltage_v,
                    current=current_a,
                    power=power_w,
                    voltage_enabled=voltage_limit_enabled,
                    current_enabled=True,
                    power_enabled=power_limit_enabled,
                    control_mode="current",
                )
            ),
            # SetState(CHARGE/DISCHARGE) is the observed energizing boundary on
            # real hardware; a second direct enable is neither required nor safer.
            WaitStep(
                duration_s,
                termination,
                mode=mode,
                activation_condition=termination_activation,
                activation_tolerance=termination_activation_tolerance,
            ),
            StandbyStep(),
            DisableStep(),
        )
    )
    return Routine(
        name=name,
        steps=tuple(steps),
    )


def constant_power_hold(
    *,
    name: str,
    limits: SafetyLimits,
    mode: OperatingState,
    power_w: float,
    voltage_limit_v: float,
    current_limit_a: float,
    duration_s: float,
    arm_duration_s: float = 30.0,
    termination: Condition | None = None,
    configure_limits: bool = True,
    voltage_limit_enabled: bool = True,
    current_limit_enabled: bool = True,
) -> Routine:
    """Build a constant-power hold with current and voltage guard channels."""
    if mode not in (OperatingState.CHARGE, OperatingState.DISCHARGE):
        raise NHRValidationError("CP hold mode must be CHARGE or DISCHARGE")
    steps: list[Step] = []
    if configure_limits:
        steps.append(ConfigureLimitsStep(limits))
    steps.extend(
        (
            ArmStep(arm_duration_s),
            MeasureStep(),
            SetpointsStep(
                Setpoints(
                    state=mode,
                    voltage=voltage_limit_v,
                    current=current_limit_a,
                    power=power_w,
                    voltage_enabled=voltage_limit_enabled,
                    current_enabled=current_limit_enabled,
                    power_enabled=True,
                    control_mode="power",
                )
            ),
            WaitStep(duration_s, termination, mode=mode),
            StandbyStep(),
            DisableStep(),
        )
    )
    return Routine(name=name, steps=tuple(steps))


def rest_period(*, name: str, duration_s: float) -> Routine:
    """Build an inactive, measured rest that always starts from disabled output."""
    return Routine(
        name=name,
        steps=(DisableStep(), WaitStep(duration_s)),
    )


def routine_from_mapping(data: Mapping[str, Any]) -> Routine:
    """Parse the deliberately small and validated v1 routine schema."""
    if not isinstance(data, Mapping):
        raise NHRValidationError("Routine definition must contain a mapping")
    allowed = {
        "name",
        "mode",
        "current_a",
        "voltage_v",
        "power_w",
        "duration_s",
        "arm_duration_s",
        "limits",
        "termination",
    }
    unknown = set(data) - allowed
    if unknown:
        raise NHRValidationError(f"Unknown routine fields: {sorted(unknown)}")
    limits_data = data.get("limits")
    if not isinstance(limits_data, Mapping):
        raise NHRValidationError("limits mapping is required")
    try:
        limits = SafetyLimits(**limits_data)
        termination_data = data.get("termination")
        termination = (
            Condition(**termination_data)
            if isinstance(termination_data, Mapping)
            else None
        )
        return constant_current_hold(
            name=str(data["name"]),
            limits=limits,
            mode=OperatingState[str(data["mode"]).upper()],
            current_a=float(data["current_a"]),
            voltage_v=float(data["voltage_v"]),
            power_w=float(data["power_w"]),
            duration_s=float(data["duration_s"]),
            arm_duration_s=float(data.get("arm_duration_s", 30.0)),
            termination=termination,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NHRValidationError(f"Invalid routine definition: {exc}") from exc


def load_yaml(path: str | Path) -> Routine:
    try:
        import yaml
    except ImportError as exc:
        raise NHRValidationError("PyYAML is required to load YAML routines") from exc
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return routine_from_mapping(data)
