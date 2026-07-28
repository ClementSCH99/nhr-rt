"""Typed routine state machine and YAML loader."""

from __future__ import annotations

import threading
import time
import uuid
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

    def evaluate(self, measurement: Measurement) -> bool:
        fields = {
            "voltage": measurement.voltage_v,
            "current": measurement.current_a,
            "power": measurement.power_w,
            "temperature": measurement.temperature_c,
        }
        if self.field not in fields:
            raise NHRValidationError(f"Unsupported condition field: {self.field}")
        actual = fields[self.field]
        if actual is None:
            return False
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
    name = "wait"

    def execute(self, context: RoutineContext) -> None:
        if self.duration_s <= 0:
            raise NHRValidationError("Wait duration must be positive")
        deadline = time.monotonic() + self.duration_s
        while time.monotonic() < deadline:
            if context.stop_event.is_set():
                raise InterruptedError("Routine stop requested")
            if context.collector.error:
                raise NHRRoutineError(context.collector.error)
            context.instrument.check_interlocks()
            measurement = context.instrument.read_measurement()
            if self.condition and self.condition.evaluate(measurement):
                return
            context.stop_event.wait(self.poll_interval_s)


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


class RoutineRunner:
    def __init__(self, instrument: NHR9300, collector: AcquisitionCollector) -> None:
        self.instrument = instrument
        self.collector = collector
        self.result: RoutineResult | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, routine: Routine) -> RoutineResult:
        if self.running:
            raise NHRRoutineError("A routine is already running")
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
        self._thread.join()
        return result

    def stop(self) -> None:
        self._stop.set()
        try:
            self.instrument.emergency_stop("Routine stop requested")
        except Exception:
            pass

    def _event(self, result: RoutineResult, step: str, message: str) -> None:
        result.events.append(RoutineEvent(datetime.now(timezone.utc), step, message))

    def _execute(self, routine: Routine, result: RoutineResult) -> None:
        result.state = RoutineState.RUNNING
        result.started_at = datetime.now(timezone.utc)
        self.collector.start()
        result.csv_path = (
            str(self.collector.csv_path) if self.collector.csv_path is not None else None
        )
        context = RoutineContext(self.instrument, self.collector, self._stop)
        try:
            for index, step in enumerate(routine.steps):
                step_name = f"{index:02d}_{step.name}"
                self.collector.set_context(result.routine_id, step_name)
                self._event(result, step_name, "started")
                step.execute(context)
                self._event(result, step_name, "completed")
            result.state = RoutineState.PASSED
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
) -> Routine:
    if mode not in (OperatingState.CHARGE, OperatingState.DISCHARGE):
        raise NHRValidationError("CC hold mode must be CHARGE or DISCHARGE")
    return Routine(
        name=name,
        steps=(
            ConfigureLimitsStep(limits),
            ArmStep(arm_duration_s),
            MeasureStep(),
            SetpointsStep(
                Setpoints(
                    state=mode,
                    voltage=voltage_v,
                    current=current_a,
                    power=power_w,
                    voltage_enabled=True,
                    current_enabled=True,
                    power_enabled=True,
                )
            ),
            EnableStep(),
            WaitStep(duration_s, termination),
            StandbyStep(),
            DisableStep(),
        ),
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
