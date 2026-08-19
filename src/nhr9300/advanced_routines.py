"""Session 5 routines: dynamic CSV profiles and independent sequences."""

from __future__ import annotations

import csv
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from .acquisition import AcquisitionCollector, AcquisitionStatistics
from .errors import NHRRoutineError, NHRValidationError
from .instrument import NHR9300
from .routines import (
    Condition,
    ConfigureLimitsStep,
    DisableStep,
    Routine,
    RoutineRunner,
    StandbyStep,
    Step,
)
from .types import Measurement, OperatingState, RoutineResult, RoutineState, SafetyLimits, Setpoints


ProfileKind = Literal["current", "power"]


@dataclass(frozen=True, slots=True)
class ProfilePoint:
    time_s: float
    value: float


def load_profile_csv(path: str | Path, kind: ProfileKind) -> tuple[ProfilePoint, ...]:
    """Load a signed current or power profile whose last row marks the end time."""
    value_field = "current_a" if kind == "current" else "power_w"
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or set(reader.fieldnames) != {"time_s", value_field}:
            raise NHRValidationError(
                f"Profile CSV must contain exactly time_s,{value_field}"
            )
        try:
            points = tuple(
                ProfilePoint(float(row["time_s"]), float(row[value_field]))
                for row in reader
            )
        except (TypeError, ValueError) as exc:
            raise NHRValidationError(f"Invalid numeric CSV profile value: {exc}") from exc
    validate_profile_points(points)
    return points


def validate_profile_points(points: Sequence[ProfilePoint]) -> None:
    if len(points) < 2:
        raise NHRValidationError("A dynamic profile requires at least two rows")
    if points[0].time_s != 0.0:
        raise NHRValidationError("The first dynamic profile time must be 0")
    for previous, current in zip(points, points[1:]):
        if not (math.isfinite(current.time_s) and math.isfinite(current.value)):
            raise NHRValidationError("Dynamic profile values must be finite")
        if current.time_s <= previous.time_s:
            raise NHRValidationError("Dynamic profile times must be strictly increasing")


@dataclass(frozen=True, slots=True)
class DynamicProfileStep(Step):
    points: Sequence[ProfilePoint]
    kind: ProfileKind
    charge_voltage_limit_v: float
    discharge_voltage_limit_v: float
    current_limit_a: float
    power_limit_w: float
    voltage_limit_enabled: bool = True
    current_limit_enabled: bool = True
    power_limit_enabled: bool = True
    termination: Condition | None = None
    poll_interval_s: float = 0.1
    arm_lease_s: float = 300.0
    name = "dynamic_profile"

    def _setpoint(
        self,
        value: float,
        *,
        mode: OperatingState | None = None,
    ) -> Setpoints:
        if mode is None:
            mode = OperatingState.CHARGE if value > 0 else OperatingState.DISCHARGE
        voltage = (
            self.charge_voltage_limit_v
            if mode == OperatingState.CHARGE
            else self.discharge_voltage_limit_v
        )
        magnitude = abs(value)
        if self.kind == "current":
            current, power = magnitude, self.power_limit_w
        else:
            current, power = self.current_limit_a, magnitude
        return Setpoints(
            state=mode,
            voltage=voltage,
            current=current,
            power=power,
            voltage_enabled=self.voltage_limit_enabled,
            current_enabled=self.current_limit_enabled,
            power_enabled=self.power_limit_enabled,
            control_mode=self.kind,
        )

    def execute(self, context) -> None:  # RoutineContext kept private to routines.py
        validate_profile_points(self.points)
        started = time.monotonic()
        active_mode: OperatingState | None = None
        baseline: float | None = None
        accumulated = 0.0
        previous_actual: float | None = None
        renew_at: float | None = None

        for point_index, (point, next_point) in enumerate(zip(self.points, self.points[1:])):
            value = point.value
            if value == 0.0:
                if active_mode is None:
                    context.instrument.disable()
                else:
                    # Keep the existing direction/contactors and change only
                    # the primary current or power request to zero.
                    context.instrument.configure_setpoints(
                        self._setpoint(0.0, mode=active_mode)
                    )
            else:
                requested_mode = (
                    OperatingState.CHARGE if value > 0 else OperatingState.DISCHARGE
                )
                if requested_mode != active_mode:
                    if previous_actual is not None and baseline is not None:
                        accumulated += max(0.0, previous_actual - baseline)
                    if active_mode is None:
                        context.instrument.disable()
                        remaining = self.points[-1].time_s - point.time_s + 2.0
                        lease = min(self.arm_lease_s, max(2.0, remaining))
                        context.instrument.arm(lease)
                        renew_at = (
                            time.monotonic() + lease - 30.0
                            if remaining > lease
                            else None
                        )
                    context.instrument.read_measurement()
                    baseline = None
                    previous_actual = None
                context.instrument.configure_setpoints(self._setpoint(value))
                active_mode = requested_mode
            context.collector.set_context(
                context.result.routine_id,
                f"dynamic_{point_index:05d}",
            )

            segment_deadline = started + next_point.time_s
            while True:
                if context.stop_event.is_set():
                    raise InterruptedError("Routine stop requested")
                if context.collector.error:
                    raise NHRRoutineError(context.collector.error)
                context.instrument.check_interlocks()
                measurement = context.instrument.read_measurement()
                if renew_at is not None and time.monotonic() >= renew_at:
                    context.instrument.renew_arm(self.arm_lease_s)
                    renew_at = time.monotonic() + self.arm_lease_s - 30.0
                if active_mode is not None and context.result.initial_active_measurement is None:
                    context.result.initial_active_measurement = measurement
                if self.termination is not None and active_mode is not None:
                    actual = self.termination.measurement_value(measurement, active_mode)
                    if actual is None:
                        raise NHRRoutineError(
                            f"Termination field {self.termination.field!r} is unavailable"
                        )
                    if self.termination.relative:
                        if baseline is None:
                            baseline = actual
                        compared = accumulated + max(0.0, actual - baseline)
                        previous_actual = actual
                    else:
                        compared = actual
                    if self.termination.evaluate_value(compared):
                        context.result.termination_reason = "condition"
                        context.result.termination_field = self.termination.field
                        context.result.termination_value = compared
                        context.result.termination_baseline = baseline
                        context.result.termination_measurement = measurement
                        return
                remaining = segment_deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                context.stop_event.wait(min(self.poll_interval_s, remaining))

        context.result.termination_reason = "profile_end"
        context.result.termination_measurement = context.instrument.read_measurement()


def dynamic_profile_routine(
    *,
    name: str,
    limits: SafetyLimits,
    points: Sequence[ProfilePoint],
    kind: ProfileKind,
    charge_voltage_limit_v: float,
    discharge_voltage_limit_v: float,
    current_limit_a: float,
    power_limit_w: float,
    voltage_limit_enabled: bool = True,
    current_limit_enabled: bool = True,
    power_limit_enabled: bool = True,
    termination: Condition | None = None,
    configure_limits: bool = True,
) -> Routine:
    steps: list[Step] = []
    if configure_limits:
        steps.append(ConfigureLimitsStep(limits))
    steps.extend(
        (
            DynamicProfileStep(
                points=points,
                kind=kind,
                charge_voltage_limit_v=charge_voltage_limit_v,
                discharge_voltage_limit_v=discharge_voltage_limit_v,
                current_limit_a=current_limit_a,
                power_limit_w=power_limit_w,
                voltage_limit_enabled=voltage_limit_enabled,
                current_limit_enabled=current_limit_enabled,
                power_limit_enabled=power_limit_enabled,
                termination=termination,
            ),
            StandbyStep(),
            DisableStep(),
        )
    )
    return Routine(name=name, steps=tuple(steps))


@dataclass(frozen=True, slots=True)
class SequenceStage:
    name: str
    routine: Routine
    mode: OperatingState | None = None


@dataclass(slots=True)
class SequenceResult:
    sequence_id: str
    state: RoutineState = RoutineState.PENDING
    reason: str = ""
    stages: list[RoutineResult] = field(default_factory=list)
    global_csv_path: str | None = None
    global_acquisition: AcquisitionStatistics | None = None
    stage_sample_counts: list[int] = field(default_factory=list)
    capacity_charge_ah: float = 0.0
    capacity_discharge_ah: float = 0.0
    energy_charge_wh: float = 0.0
    energy_discharge_wh: float = 0.0


def _csv_directional_totals(path: str | None) -> dict[str, float]:
    totals = {
        "capacity_charge_ah": 0.0,
        "capacity_discharge_ah": 0.0,
        "energy_charge_wh": 0.0,
        "energy_discharge_wh": 0.0,
    }
    if path is None:
        return totals
    previous: dict[str, float] = {}
    previous_state = ""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            state = row["state"]
            if state not in {"charge", "discharge"}:
                previous_state = state
                continue
            pairs = (
                ("capacity_charge_ah", 1.0),
                ("capacity_discharge_ah", 1.0),
                ("energy_charge_kwh", 1000.0),
                ("energy_discharge_kwh", 1000.0),
            )
            for field_name, scale in pairs:
                raw = row[field_name]
                if raw == "":
                    continue
                current = abs(float(raw))
                delta = current if state != previous_state else max(
                    0.0, current - previous.get(field_name, current)
                )
                if state == "charge" and "charge" in field_name:
                    target = "energy_charge_wh" if "energy" in field_name else "capacity_charge_ah"
                    totals[target] += delta * scale
                elif state == "discharge" and "discharge" in field_name:
                    target = "energy_discharge_wh" if "energy" in field_name else "capacity_discharge_ah"
                    totals[target] += delta * scale
                previous[field_name] = current
            previous_state = state
    return totals


def _stage_csv_path(global_path: Path, index: int, name: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "stage"
    return global_path.with_name(
        f"{global_path.stem}__{index + 1:02d}_{safe_name}{global_path.suffix}"
    )


def _split_global_csv(
    global_path: str,
    stages: Sequence[SequenceStage],
    results: Sequence[RoutineResult],
) -> list[int]:
    path = Path(global_path)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    if fieldnames is None:
        raise NHRRoutineError("Global acquisition CSV has no header")
    counts: list[int] = []
    for index, (stage, result) in enumerate(zip(stages, results)):
        stage_rows = [row for row in rows if row["routine_id"] == result.routine_id]
        stage_path = _stage_csv_path(path, index, stage.name)
        with stage_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(stage_rows)
        result.csv_path = str(stage_path.resolve())
        counts.append(len(stage_rows))
    return counts


class SequenceRunner:
    """Run independent routines in order and stop at the first failed stage."""

    def __init__(self, instrument: NHR9300, collector: AcquisitionCollector) -> None:
        self.instrument = instrument
        self.collector = collector

    def run(self, stages: Sequence[SequenceStage]) -> SequenceResult:
        if not stages:
            raise NHRValidationError("A sequence requires at least one stage")
        result = SequenceResult(sequence_id=str(uuid.uuid4()), state=RoutineState.RUNNING)
        self.collector.start()
        result.global_csv_path = (
            str(self.collector.csv_path.resolve())
            if self.collector.csv_path is not None
            else None
        )
        try:
            for stage in stages:
                stage_result = RoutineRunner(
                    self.instrument,
                    self.collector,
                    manage_collector=False,
                ).run(stage.routine)
                result.stages.append(stage_result)
                if stage_result.state != RoutineState.PASSED:
                    result.state = stage_result.state
                    result.reason = (
                        f"Stage {stage.name!r} {stage_result.state.value}: "
                        f"{stage_result.reason}"
                    )
                    break
            else:
                result.state = RoutineState.PASSED
                result.reason = "All sequence stages passed"
        finally:
            self.collector.stop()
            result.global_acquisition = self.collector.statistics()
        if result.global_csv_path is None:
            raise NHRRoutineError("Sequence acquisition did not produce a global CSV")
        executed_stages = stages[: len(result.stages)]
        result.stage_sample_counts = _split_global_csv(
            result.global_csv_path,
            executed_stages,
            result.stages,
        )
        for stage_result in result.stages:
            totals = _csv_directional_totals(stage_result.csv_path)
            result.capacity_charge_ah += totals["capacity_charge_ah"]
            result.capacity_discharge_ah += totals["capacity_discharge_ah"]
            result.energy_charge_wh += totals["energy_charge_wh"]
            result.energy_discharge_wh += totals["energy_discharge_wh"]
        return result
