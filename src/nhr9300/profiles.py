"""Validated JSON contracts for reusable supervised NHR workflows."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .sequences import (
    SequenceStage,
    dynamic_profile_routine,
    load_profile_csv,
)
from .errors import NHRValidationError
from .external_interlocks import (
    APPLIES,
    COMPARISONS,
    SOURCE_ID_PATTERN,
    ExternalInterlockRule,
)
from .routines import Condition, constant_current_hold, constant_power_hold, rest_period
from .types import OperatingState, SafetyLimits


STAGE_TYPES = {
    "constant_current",
    "cccv",
    "constant_power",
    "rest",
    "csv_profile",
}
ACTIVE_MODES = {OperatingState.CHARGE, OperatingState.DISCHARGE}


@dataclass(frozen=True, slots=True)
class WorkflowLimits:
    max_current_a: float
    max_power_w: float
    max_stage_duration_s: float
    max_sequence_duration_s: float
    approved: bool
    profile_name: str


@dataclass(frozen=True, slots=True)
class StageProfile:
    name: str
    type: str
    duration_s: float
    mode: OperatingState | None = None
    current_a: float | None = None
    voltage_v: float | None = None
    power_w: float | None = None
    cutoff_current_a: float | None = None
    voltage_limit_enabled: bool | None = None
    current_limit_enabled: bool | None = None
    power_limit_enabled: bool | None = None
    termination: Condition | None = None
    csv_path: Path | None = None
    profile_kind: str | None = None
    charge_voltage_limit_v: float | None = None
    discharge_voltage_limit_v: float | None = None


@dataclass(frozen=True, slots=True)
class WorkflowConfiguration:
    test_description: str
    bench_description: str
    stop_procedure: str
    expected_resource: str
    expected_serial_number: str
    simulation_initial_voltage_v: float
    watchdog_enabled: bool
    safety_limits: SafetyLimits
    workflow_limits: WorkflowLimits
    stages: tuple[StageProfile, ...]
    external_interlocks: tuple[ExternalInterlockRule, ...]
    source_path: Path

    def sequence(self, *, configure_limits: bool) -> tuple[SequenceStage, ...]:
        configured = not configure_limits
        built: list[SequenceStage] = []
        for stage in self.stages:
            include_limits = not configured and stage.type != "rest"
            if stage.type in {"constant_current", "cccv"}:
                termination = (
                    Condition("cutoff_current", "<=", stage.cutoff_current_a)
                    if stage.type == "cccv"
                    else stage.termination
                )
                termination_activation = None
                if stage.type == "cccv":
                    voltage_operator = (
                        ">=" if stage.mode == OperatingState.CHARGE else "<="
                    )
                    termination_activation = Condition(
                        "voltage",
                        voltage_operator,
                        stage.voltage_v,
                    )
                routine = constant_current_hold(
                    name=stage.name,
                    limits=self.safety_limits,
                    mode=stage.mode,
                    current_a=stage.current_a,
                    voltage_v=stage.voltage_v,
                    power_w=stage.power_w or 0.0,
                    duration_s=stage.duration_s,
                    arm_duration_s=min(300.0, stage.duration_s + 5.0),
                    termination=termination,
                    termination_activation=termination_activation,
                    termination_activation_tolerance=(
                        1e-6 if stage.type == "cccv" else 0.0
                    ),
                    configure_limits=include_limits,
                    voltage_limit_enabled=stage.voltage_limit_enabled,
                    power_limit_enabled=stage.power_limit_enabled,
                )
            elif stage.type == "constant_power":
                routine = constant_power_hold(
                    name=stage.name,
                    limits=self.safety_limits,
                    mode=stage.mode,
                    power_w=stage.power_w,
                    voltage_limit_v=stage.voltage_v,
                    current_limit_a=stage.current_a or 0.0,
                    duration_s=stage.duration_s,
                    arm_duration_s=min(300.0, stage.duration_s + 5.0),
                    termination=stage.termination,
                    configure_limits=include_limits,
                    voltage_limit_enabled=stage.voltage_limit_enabled,
                    current_limit_enabled=stage.current_limit_enabled,
                )
            elif stage.type == "rest":
                routine = rest_period(name=stage.name, duration_s=stage.duration_s)
            else:
                points = load_profile_csv(stage.csv_path, stage.profile_kind)
                routine = dynamic_profile_routine(
                    name=stage.name,
                    limits=self.safety_limits,
                    points=points,
                    kind=stage.profile_kind,
                    charge_voltage_limit_v=stage.charge_voltage_limit_v,
                    discharge_voltage_limit_v=stage.discharge_voltage_limit_v,
                    current_limit_a=stage.current_a or 0.0,
                    power_limit_w=stage.power_w or 0.0,
                    voltage_limit_enabled=stage.voltage_limit_enabled,
                    current_limit_enabled=stage.current_limit_enabled,
                    power_limit_enabled=stage.power_limit_enabled,
                    termination=stage.termination,
                    configure_limits=include_limits,
                )
            configured = configured or include_limits
            built.append(SequenceStage(stage.name, routine, stage.mode))
        return tuple(built)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NHRValidationError(f"{name} must be an object")
    return value


def _positive(value: Any, name: str) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise NHRValidationError(f"{name} must be a number greater than zero") from exc
    if converted <= 0.0:
        raise NHRValidationError(f"{name} must be greater than zero")
    return converted


def _required_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise NHRValidationError(f"{name} must be true or false")
    return value


def load_workflow_profile(path: str | Path) -> WorkflowConfiguration:
    source = Path(path).resolve()
    root = _mapping(json.loads(source.read_text(encoding="utf-8")), "workflow profile")
    allowed_root = {
        "test_description", "bench_description", "stop_procedure",
        "expected_resource", "expected_serial_number", "simulation_initial_voltage_v",
        "watchdog_enabled", "safety_limits", "workflow_limits", "stages",
        "external_interlocks",
    }
    unknown_root = set(root) - allowed_root
    if unknown_root:
        raise NHRValidationError(f"Unknown workflow profile fields: {sorted(unknown_root)}")
    limits = SafetyLimits(**_mapping(root.get("safety_limits"), "safety_limits"))
    workflow = WorkflowLimits(**_mapping(root.get("workflow_limits"), "workflow_limits"))
    raw_stages = root.get("stages")
    if not isinstance(raw_stages, list):
        raise NHRValidationError("stages must be an array")
    stages: list[StageProfile] = []
    for index, raw in enumerate(raw_stages):
        item = dict(_mapping(raw, f"stages[{index}]"))
        termination_data = item.pop("termination", None)
        termination = (
            Condition(**_mapping(termination_data, f"stages[{index}].termination"))
            if termination_data is not None
            else None
        )
        mode_value = item.pop("mode", None)
        mode = OperatingState[str(mode_value).upper()] if mode_value is not None else None
        csv_value = item.pop("csv_path", None)
        csv_path = (source.parent / str(csv_value)).resolve() if csv_value else None
        try:
            stages.append(
                StageProfile(
                    mode=mode,
                    termination=termination,
                    csv_path=csv_path,
                    **item,
                )
            )
        except TypeError as exc:
            raise NHRValidationError(f"Invalid stages[{index}]: {exc}") from exc
    raw_interlocks = root.get("external_interlocks", [])
    if not isinstance(raw_interlocks, list):
        raise NHRValidationError("external_interlocks must be an array")
    interlocks: list[ExternalInterlockRule] = []
    allowed_rule_fields = {
        "rule_id", "source_id", "signal", "comparison", "unit", "applies",
        "max_age_s", "stability_duration_s", "minimum", "maximum", "expected",
    }
    for index, raw in enumerate(raw_interlocks):
        item = dict(_mapping(raw, f"external_interlocks[{index}]"))
        unknown = set(item) - allowed_rule_fields
        if unknown:
            raise NHRValidationError(
                f"Unknown external_interlocks[{index}] fields: {sorted(unknown)}"
            )
        for name in ("rule_id", "source_id", "signal", "comparison", "unit", "applies"):
            if name not in item or not isinstance(item[name], str):
                raise NHRValidationError(
                    f"external_interlocks[{index}].{name} must be a string"
                )
        for name in ("max_age_s", "stability_duration_s", "minimum", "maximum"):
            if name not in item:
                continue
            value = item[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise NHRValidationError(
                    f"external_interlocks[{index}].{name} must be a number"
                )
            item[name] = float(value)
        try:
            interlocks.append(ExternalInterlockRule(**item))
        except TypeError as exc:
            raise NHRValidationError(
                f"Invalid external_interlocks[{index}]: {exc}"
            ) from exc
    try:
        return WorkflowConfiguration(
            test_description=str(root.get("test_description", "")),
            bench_description=str(root.get("bench_description", "")),
            stop_procedure=str(root.get("stop_procedure", "")),
            expected_resource=str(root.get("expected_resource", "")),
            expected_serial_number=str(root.get("expected_serial_number", "")),
            simulation_initial_voltage_v=float(root.get("simulation_initial_voltage_v", 90.0)),
            watchdog_enabled=bool(root.get("watchdog_enabled", False)),
            safety_limits=limits,
            workflow_limits=workflow,
            stages=tuple(stages),
            external_interlocks=tuple(interlocks),
            source_path=source,
        )
    except (TypeError, ValueError) as exc:
        raise NHRValidationError(f"Invalid workflow profile: {exc}") from exc


def validate_workflow_profile(configuration: WorkflowConfiguration, *, hardware: bool) -> None:
    limits = configuration.safety_limits
    workflow = configuration.workflow_limits
    if not limits.approved or not limits.profile_name.strip():
        raise NHRValidationError("safety_limits must be explicitly approved and named")
    if not workflow.approved or not workflow.profile_name.strip():
        raise NHRValidationError("workflow_limits must be explicitly approved and named")
    for value, name in (
        (workflow.max_current_a, "max_current_a"),
        (workflow.max_power_w, "max_power_w"),
        (workflow.max_stage_duration_s, "max_stage_duration_s"),
        (workflow.max_sequence_duration_s, "max_sequence_duration_s"),
    ):
        _positive(value, name)
    if not configuration.stages:
        raise NHRValidationError("At least one stage is required")

    rule_ids: set[str] = set()
    for rule in configuration.external_interlocks:
        if not rule.rule_id.strip() or rule.rule_id in rule_ids:
            raise NHRValidationError(
                "External interlock rule_id values must be non-empty and unique"
            )
        rule_ids.add(rule.rule_id)
        if not rule.source_id.strip() or not rule.signal.strip() or not rule.unit.strip():
            raise NHRValidationError(
                f"External interlock {rule.rule_id!r} requires source_id, signal and unit"
            )
        if not SOURCE_ID_PATTERN.fullmatch(rule.source_id):
            raise NHRValidationError(
                f"External interlock {rule.rule_id!r} has invalid source_id"
            )
        if rule.comparison not in COMPARISONS:
            raise NHRValidationError(
                f"External interlock {rule.rule_id!r} has unsupported comparison"
            )
        if rule.applies not in APPLIES:
            raise NHRValidationError(
                f"External interlock {rule.rule_id!r} has unsupported applies value"
            )
        if rule.max_age_s <= 0 or not math.isfinite(rule.max_age_s):
            raise NHRValidationError(
                f"External interlock {rule.rule_id!r} max_age_s must be finite and positive"
            )
        if (
            rule.stability_duration_s < 0
            or not math.isfinite(rule.stability_duration_s)
        ):
            raise NHRValidationError(
                f"External interlock {rule.rule_id!r} stability_duration_s must be finite and non-negative"
            )
        if rule.comparison == "equals":
            if not isinstance(rule.expected, bool):
                raise NHRValidationError(
                    f"External interlock {rule.rule_id!r} equals requires boolean expected"
                )
            if rule.minimum is not None or rule.maximum is not None:
                raise NHRValidationError(
                    f"External interlock {rule.rule_id!r} equals cannot define numeric bounds"
                )
        else:
            if rule.expected is not None:
                raise NHRValidationError(
                    f"External interlock {rule.rule_id!r} numeric rule cannot define expected"
                )
            for value, name in ((rule.minimum, "minimum"), (rule.maximum, "maximum")):
                if value is not None and not math.isfinite(float(value)):
                    raise NHRValidationError(
                        f"External interlock {rule.rule_id!r} {name} must be finite"
                    )
            if rule.comparison == "minimum" and rule.minimum is None:
                raise NHRValidationError(
                    f"External interlock {rule.rule_id!r} requires minimum"
                )
            if rule.comparison == "maximum" and rule.maximum is None:
                raise NHRValidationError(
                    f"External interlock {rule.rule_id!r} requires maximum"
                )
            if rule.comparison == "range":
                if rule.minimum is None or rule.maximum is None:
                    raise NHRValidationError(
                        f"External interlock {rule.rule_id!r} range requires minimum and maximum"
                    )
                if rule.minimum > rule.maximum:
                    raise NHRValidationError(
                        f"External interlock {rule.rule_id!r} minimum exceeds maximum"
                    )
    if sum(stage.duration_s for stage in configuration.stages) > workflow.max_sequence_duration_s:
        raise NHRValidationError("Sequence duration exceeds max_sequence_duration_s")

    names: set[str] = set()
    for stage in configuration.stages:
        if not stage.name.strip() or stage.name in names:
            raise NHRValidationError("Stage names must be non-empty and unique")
        names.add(stage.name)
        if stage.type not in STAGE_TYPES:
            raise NHRValidationError(f"Unsupported stage type: {stage.type}")
        if not 0.0 < stage.duration_s <= workflow.max_stage_duration_s:
            raise NHRValidationError(f"Stage {stage.name!r} has an invalid duration")
        if stage.type == "rest":
            if stage.termination is not None:
                raise NHRValidationError("A rest stage cannot have a termination condition")
            continue
        voltage_enabled = _required_bool(
            stage.voltage_limit_enabled,
            f"{stage.name}.voltage_limit_enabled",
        )
        current_enabled = _required_bool(
            stage.current_limit_enabled,
            f"{stage.name}.current_limit_enabled",
        )
        power_enabled = _required_bool(
            stage.power_limit_enabled,
            f"{stage.name}.power_limit_enabled",
        )
        if not any((voltage_enabled, current_enabled, power_enabled)):
            raise NHRValidationError(
                f"Stage {stage.name!r} must enable at least one operating limit"
            )
        if not voltage_enabled:
            raise NHRValidationError(
                f"Stage {stage.name!r} requires voltage_limit_enabled=true"
            )
        if stage.type in {"constant_current", "cccv", "constant_power"}:
            if stage.duration_s > 295.0:
                raise NHRValidationError(
                    f"Static stage {stage.name!r} cannot exceed 295 s"
                )
            if stage.mode not in ACTIVE_MODES:
                raise NHRValidationError(f"Stage {stage.name!r} requires charge or discharge mode")
            voltage = _positive(stage.voltage_v, f"{stage.name}.voltage_v")
            if stage.type in {"constant_current", "cccv"}:
                if not current_enabled:
                    raise NHRValidationError(
                        f"Stage {stage.name!r} requires current_limit_enabled=true"
                    )
                current = _positive(stage.current_a, f"{stage.name}.current_a")
                power = (
                    _positive(stage.power_w, f"{stage.name}.power_w")
                    if power_enabled
                    else 0.0
                )
            else:
                if not power_enabled:
                    raise NHRValidationError(
                        f"Stage {stage.name!r} requires power_limit_enabled=true"
                    )
                power = _positive(stage.power_w, f"{stage.name}.power_w")
                current = (
                    _positive(stage.current_a, f"{stage.name}.current_a")
                    if current_enabled
                    else 0.0
                )
            if current > workflow.max_current_a or power > workflow.max_power_w:
                raise NHRValidationError(f"Stage {stage.name!r} exceeds workflow limits")
            if stage.mode == OperatingState.CHARGE:
                if current > limits.charge_current or power > limits.charge_power or voltage > limits.charge_voltage_max:
                    raise NHRValidationError(f"Stage {stage.name!r} exceeds charge safety limits")
            elif current > limits.discharge_current or power > limits.discharge_power or voltage < limits.discharge_voltage_min:
                raise NHRValidationError(f"Stage {stage.name!r} exceeds discharge safety limits")
            if stage.type == "cccv":
                if stage.termination is not None:
                    raise NHRValidationError(
                        "CCCV uses cutoff_current_a instead of a termination object"
                    )
                cutoff = _positive(
                    stage.cutoff_current_a,
                    f"{stage.name}.cutoff_current_a",
                )
                if cutoff >= current:
                    raise NHRValidationError(
                        "CCCV cutoff_current_a must be lower than current_a"
                    )
            elif stage.cutoff_current_a is not None:
                raise NHRValidationError(
                    "cutoff_current_a is supported only by CCCV stages"
                )
        else:
            if stage.csv_path is None or not stage.csv_path.is_file():
                raise NHRValidationError(f"Stage {stage.name!r} CSV file does not exist")
            if stage.profile_kind not in {"current", "power"}:
                raise NHRValidationError("csv_profile profile_kind must be current or power")
            points = load_profile_csv(stage.csv_path, stage.profile_kind)
            if points[-1].time_s != stage.duration_s:
                raise NHRValidationError("CSV final time must equal the stage duration_s")
            peak = max(abs(point.value) for point in points)
            cap = workflow.max_current_a if stage.profile_kind == "current" else workflow.max_power_w
            if peak > cap:
                raise NHRValidationError(f"Stage {stage.name!r} CSV exceeds workflow limits")
            if stage.profile_kind == "current" and not current_enabled:
                raise NHRValidationError(
                    "A current CSV requires current_limit_enabled=true"
                )
            if stage.profile_kind == "power" and not power_enabled:
                raise NHRValidationError(
                    "A power CSV requires power_limit_enabled=true"
                )
            current_limit = (
                _positive(stage.current_a, f"{stage.name}.current_a")
                if current_enabled and stage.profile_kind == "power"
                else peak if stage.profile_kind == "current" else 0.0
            )
            power_limit = (
                _positive(stage.power_w, f"{stage.name}.power_w")
                if power_enabled and stage.profile_kind == "current"
                else peak if stage.profile_kind == "power" else 0.0
            )
            if current_limit > workflow.max_current_a:
                raise NHRValidationError(f"Stage {stage.name!r} current limit is too high")
            if power_limit > workflow.max_power_w:
                raise NHRValidationError(f"Stage {stage.name!r} power limit is too high")
            if current_limit > min(limits.charge_current, limits.discharge_current):
                raise NHRValidationError(f"Stage {stage.name!r} current limit exceeds safety limits")
            if power_limit > min(limits.charge_power, limits.discharge_power):
                raise NHRValidationError(f"Stage {stage.name!r} power limit exceeds safety limits")
            for point in points:
                directional_limit = (
                    limits.charge_current if point.value > 0 else limits.discharge_current
                ) if stage.profile_kind == "current" else (
                    limits.charge_power if point.value > 0 else limits.discharge_power
                )
                if abs(point.value) > directional_limit:
                    raise NHRValidationError(
                        f"Stage {stage.name!r} CSV exceeds directional safety limits"
                    )
            if not limits.discharge_voltage_min <= stage.discharge_voltage_limit_v <= stage.charge_voltage_limit_v <= limits.charge_voltage_max:
                raise NHRValidationError(f"Stage {stage.name!r} voltage window is invalid")

        condition = stage.termination
        if condition is not None:
            if condition.field not in {"voltage", "capacity_ah", "energy_wh"}:
                raise NHRValidationError(f"Unsupported termination field: {condition.field}")
            if condition.value <= 0.0:
                raise NHRValidationError("Termination value must be greater than zero")
            if condition.field in {"capacity_ah", "energy_wh"}:
                if not condition.relative:
                    raise NHRValidationError(f"{condition.field} termination requires relative=true")
                if condition.operator not in {">", ">="}:
                    raise NHRValidationError(f"{condition.field} termination must use > or >=")
            elif condition.relative:
                raise NHRValidationError(
                    "Relative termination is supported only for capacity_ah and energy_wh"
                )
            if condition.field == "voltage" and stage.mode in ACTIVE_MODES:
                expected = {">", ">="} if stage.mode == OperatingState.CHARGE else {"<", "<="}
                if condition.operator not in expected:
                    raise NHRValidationError("Voltage termination direction is inconsistent with mode")
                if not limits.discharge_voltage_min <= condition.value <= limits.charge_voltage_max:
                    raise NHRValidationError("Voltage termination must remain inside safety limits")

    if hardware:
        if not configuration.watchdog_enabled:
            raise NHRValidationError("The hardware workflow runner requires the watchdog")
        required = {
            "bench_description": configuration.bench_description,
            "stop_procedure": configuration.stop_procedure,
            "expected_resource": configuration.expected_resource,
            "expected_serial_number": configuration.expected_serial_number,
        }
        invalid = [name for name, value in required.items() if not value.strip() or value.strip().upper().startswith("REPLACE_")]
        if invalid:
            raise NHRValidationError("Hardware profile fields require reviewed values: " + ", ".join(invalid))
