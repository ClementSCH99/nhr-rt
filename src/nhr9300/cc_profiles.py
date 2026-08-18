"""Validated profile contract for supervised constant-current routines."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import NHRValidationError
from .routines import Condition, Routine, constant_current_hold
from .types import OperatingState, SafetyLimits


MAX_CURRENT_A = 5.0
MAX_POWER_W = 500.0
MAX_DURATION_S = 60.0
MAX_ARM_DURATION_S = 70.0
HARDWARE_TERMINATION_FIELDS = {"voltage", "capacity_ah", "energy_wh"}
SIMULATION_TERMINATION_FIELDS = HARDWARE_TERMINATION_FIELDS | {"temperature"}


@dataclass(frozen=True, slots=True)
class CCHoldProfile:
    mode: OperatingState
    current_a: float
    voltage_v: float
    power_w: float
    max_duration_s: float
    arm_duration_s: float
    current_tolerance_a: float
    current_settling_time_s: float
    minimum_active_samples: int
    termination: Condition | None
    watchdog_enabled: bool
    ignore_uut_temperature: bool
    approved: bool
    profile_name: str
    simulation_only: bool = False

    def routine(self, limits: SafetyLimits, *, configure_limits: bool) -> Routine:
        return constant_current_hold(
            name=self.profile_name,
            limits=limits,
            mode=self.mode,
            current_a=self.current_a,
            voltage_v=self.voltage_v,
            power_w=self.power_w,
            duration_s=self.max_duration_s,
            arm_duration_s=self.arm_duration_s,
            termination=self.termination,
            configure_limits=configure_limits,
        )


@dataclass(frozen=True, slots=True)
class CCProfileConfiguration:
    test_description: str
    bench_description: str
    stop_procedure: str
    expected_resource: str
    expected_serial_number: str
    simulation_initial_voltage_v: float
    safety_limits: SafetyLimits
    cc_hold: CCHoldProfile
    review_note: str = ""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NHRValidationError(f"{name} must be an object")
    return value


def load_cc_profile(path: str | Path) -> CCProfileConfiguration:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    root = _mapping(data, "CC profile")
    limits_data = _mapping(root.get("safety_limits"), "safety_limits")
    hold_data = dict(_mapping(root.get("cc_hold"), "cc_hold"))
    termination_data = hold_data.pop("termination", None)
    try:
        termination = (
            Condition(**_mapping(termination_data, "cc_hold.termination"))
            if termination_data is not None
            else None
        )
        hold_data["mode"] = OperatingState[str(hold_data["mode"]).upper()]
        hold_data["termination"] = termination
        return CCProfileConfiguration(
            test_description=str(root.get("test_description", "")),
            bench_description=str(root.get("bench_description", "")),
            stop_procedure=str(root.get("stop_procedure", "")),
            expected_resource=str(root.get("expected_resource", "")),
            expected_serial_number=str(root.get("expected_serial_number", "")),
            simulation_initial_voltage_v=float(
                root.get("simulation_initial_voltage_v", 90.0)
            ),
            safety_limits=SafetyLimits(**limits_data),
            cc_hold=CCHoldProfile(**hold_data),
            review_note=str(root.get("review_note", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NHRValidationError(f"Invalid CC profile: {exc}") from exc


def validate_cc_profile(
    configuration: CCProfileConfiguration,
    *,
    hardware: bool,
) -> None:
    limits = configuration.safety_limits
    hold = configuration.cc_hold
    if not limits.approved or not limits.profile_name.strip():
        raise NHRValidationError("safety_limits must be explicitly approved and named")
    if not hold.approved or not hold.profile_name.strip():
        raise NHRValidationError("cc_hold must be explicitly approved and named")
    if hold.mode not in (OperatingState.CHARGE, OperatingState.DISCHARGE):
        raise NHRValidationError("The CC runner supports CHARGE or DISCHARGE only")
    if not 0.0 < hold.current_a <= MAX_CURRENT_A:
        raise NHRValidationError(
            f"CC current must be greater than 0 and at most {MAX_CURRENT_A} A"
        )
    if not 0.0 < hold.power_w <= MAX_POWER_W:
        raise NHRValidationError(
            f"CC power must be greater than 0 and at most {MAX_POWER_W} W"
        )
    if not 0.0 < hold.max_duration_s <= MAX_DURATION_S:
        raise NHRValidationError(
            f"CC maximum duration must be at most {MAX_DURATION_S} s"
        )
    if not (
        hold.max_duration_s + 2.0
        <= hold.arm_duration_s
        <= MAX_ARM_DURATION_S
    ):
        raise NHRValidationError(
            "arm_duration_s must be at least max_duration_s + 2 s and at most "
            f"{MAX_ARM_DURATION_S} s"
        )
    if not 0.0 < hold.current_tolerance_a <= MAX_CURRENT_A:
        raise NHRValidationError(
            f"current_tolerance_a must be greater than 0 and at most {MAX_CURRENT_A} A"
        )
    if not 0.0 <= hold.current_settling_time_s <= 2.0:
        raise NHRValidationError(
            "current_settling_time_s must be between 0 and 2 s"
        )
    if hold.current_settling_time_s >= hold.max_duration_s:
        raise NHRValidationError(
            "current_settling_time_s must be shorter than max_duration_s"
        )
    if hold.minimum_active_samples < 1:
        raise NHRValidationError("minimum_active_samples must be at least 1")
    if hold.mode == OperatingState.CHARGE:
        if hold.current_a > limits.charge_current:
            raise NHRValidationError("CC current exceeds the approved charge limit")
        if hold.power_w > limits.charge_power:
            raise NHRValidationError("CC power exceeds the approved charge limit")
        if hold.voltage_v > limits.charge_voltage_max:
            raise NHRValidationError("CC voltage exceeds the approved charge limit")
    else:
        if hold.current_a > limits.discharge_current:
            raise NHRValidationError("CC current exceeds the approved discharge limit")
        if hold.power_w > limits.discharge_power:
            raise NHRValidationError("CC power exceeds the approved discharge limit")
        if hold.voltage_v < limits.discharge_voltage_min:
            raise NHRValidationError("CC voltage is below the approved discharge limit")

    if hold.ignore_uut_temperature and limits.uut_temperature_max is not None:
        raise NHRValidationError(
            "uut_temperature_max must be null while the unwired UUT temperature is ignored"
        )
    if hardware and not hold.ignore_uut_temperature:
        raise NHRValidationError(
            "The current bench requires ignore_uut_temperature=true"
        )
    if hardware and hold.simulation_only:
        raise NHRValidationError("A simulation-only profile cannot run on hardware")
    if hardware and not hold.watchdog_enabled:
        raise NHRValidationError("The supervised CC hardware runner requires the watchdog")

    if hold.termination is not None:
        condition = hold.termination
        allowed = (
            HARDWARE_TERMINATION_FIELDS
            if hardware
            else SIMULATION_TERMINATION_FIELDS
        )
        if condition.field not in allowed:
            raise NHRValidationError(
                f"Unsupported CC termination field: {condition.field}"
            )
        if condition.value <= 0.0:
            raise NHRValidationError("Termination value must be greater than zero")
        if (
            condition.field in {"capacity_ah", "energy_wh"}
            and condition.operator not in {">", ">="}
        ):
            raise NHRValidationError(
                f"{condition.field} termination must use > or >="
            )
        if condition.field in {"capacity_ah", "energy_wh"} and not condition.relative:
            raise NHRValidationError(
                f"{condition.field} termination must use relative=true"
            )
        if condition.field not in {"capacity_ah", "energy_wh"} and condition.relative:
            raise NHRValidationError(
                "Relative termination is supported only for capacity_ah and energy_wh"
            )
        if condition.field == "voltage":
            expected = (
                {">", ">="}
                if hold.mode == OperatingState.CHARGE
                else {"<", "<="}
            )
            if condition.operator not in expected:
                raise NHRValidationError(
                    "Voltage termination direction is inconsistent with the CC mode"
                )
            if not (
                limits.discharge_voltage_min
                <= condition.value
                <= limits.charge_voltage_max
            ):
                raise NHRValidationError(
                    "Voltage termination must remain inside the approved voltage window"
                )
        if condition.field == "temperature" and not hold.simulation_only:
            raise NHRValidationError(
                "Temperature termination is simulation-only while the UUT sensor is unwired"
            )

    if hardware:
        required_text = {
            "bench_description": configuration.bench_description,
            "stop_procedure": configuration.stop_procedure,
            "expected_resource": configuration.expected_resource,
            "expected_serial_number": configuration.expected_serial_number,
        }
        missing = [name for name, value in required_text.items() if not value.strip()]
        placeholders = [
            name
            for name, value in required_text.items()
            if value.strip().upper().startswith("REPLACE_")
        ]
        if missing or placeholders:
            names = sorted(set(missing + placeholders))
            raise NHRValidationError(
                "Hardware profile fields require reviewed values: " + ", ".join(names)
            )
