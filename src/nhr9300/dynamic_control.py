"""Service-owned dynamic operating controls for approved workflows.

The external numeric reader is shared with future BMS-driven stages.  SoP is an
operating ceiling; the approved NHR safety limits are never rewritten here.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .external_interlocks import ExternalInterlockManager
from .instrument import NHR9300
from .types import OperatingState, SafetyLimits, Setpoints


@dataclass(frozen=True, slots=True)
class SoPConfiguration:
    source_id: str
    charge_signal: str
    discharge_signal: str
    max_age_s: float
    min_charge_w: float
    min_discharge_w: float
    low_cycles: int
    unit: str = "W"
    charge_value_sign: str = "positive"
    discharge_value_sign: str = "positive"


class SoPTerminalRest(Exception):
    """A required operating ceiling cannot support the active stage."""


class SoPController:
    """Apply a workflow's SoP ceiling to each service-owned stage setpoint."""

    def __init__(
        self, configuration: SoPConfiguration, source: ExternalInterlockManager,
        instrument: NHR9300, safety_limits: SafetyLimits, workflow_max_power_w: float,
        arm_lease_supervisor: Any = None,
    ) -> None:
        self.configuration = configuration
        self.source = source
        self.instrument = instrument
        self.safety_limits = safety_limits
        self.workflow_max_power_w = workflow_max_power_w
        self.arm_lease_supervisor = arm_lease_supervisor
        self.events: list[dict[str, Any]] = []
        self.fault: dict[str, Any] | None = None
        self._base: Setpoints | None = None
        self._applied: Setpoints | None = None
        self._stage: str | None = None
        self._low_count = 0
        self._next_tick: float | None = None
        self._current_limit: dict[str, Any] | None = None
        self._profile_point: tuple[int, float, OperatingState] | None = None

    def _reading(self, mode: OperatingState) -> tuple[dict[str, Any], float, float]:
        config = self.configuration
        signal = config.charge_signal if mode == OperatingState.CHARGE else config.discharge_signal
        minimum = config.min_charge_w if mode == OperatingState.CHARGE else config.min_discharge_w
        reading = dict(self.source.numeric_signal(config.source_id, signal, config.max_age_s))
        reading["unit"] = config.unit
        expected_sign = (config.charge_value_sign if mode == OperatingState.CHARGE
                         else config.discharge_value_sign)
        reading["expected_sign"] = expected_sign
        raw_value = reading["value"]
        if reading["reason"] is not None:
            self._terminal(str(reading["reason"]), reading)
        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool) or not math.isfinite(raw_value):
            self._terminal("signal_not_numeric", reading)
        value = float(raw_value) * (1000.0 if config.unit == "kW" else 1.0)
        if expected_sign == "negative":
            value = -value
        if not math.isfinite(value):
            self._terminal("signal_out_of_range", reading)
        reading["normalized_w"] = value
        if value <= 0:
            self._terminal("sop_zero_or_negative", reading)
        return reading, value, minimum

    def _record(self, kind: str, **fields: Any) -> None:
        self.events.append({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "kind": kind, "stage": self._stage, **fields,
        })

    def _terminal(self, reason: str, reading: dict[str, Any]) -> None:
        self.fault = {"reason": reason, "reading": dict(reading),
                      "low_cycles": self._low_count, "stage": self._stage}
        self._record("terminal_rest_requested", **self.fault)
        if self._base is not None:
            # A falling or unusable ceiling must stop energy flow immediately
            # on the control thread, before sequence bookkeeping or final rest.
            self.instrument.disable()
            self._record("output_disabled_for_terminal_rest", reason=reason)
        raise SoPTerminalRest(f"SoP terminal rest: {reason}")

    def require_start_ready(self) -> None:
        """Refuse a run before energy flows when either required direction is bad."""
        for mode in (OperatingState.CHARGE, OperatingState.DISCHARGE):
            reading, value, minimum = self._reading(mode)
            if value < minimum:
                self._terminal("sop_below_start_minimum", reading)

    def begin_stage(self, name: str) -> None:
        self._stage = name
        self._base = None
        self._applied = None
        self._low_count = 0
        self._next_tick = None
        self._profile_point = None

    def end_stage(self) -> None:
        self._stage = None
        self._base = None
        self._applied = None
        self._next_tick = None
        self._current_limit = None
        self._profile_point = None

    def apply_base(
        self, base: Setpoints, *,
        profile_point: tuple[int, float, OperatingState] | None = None,
    ) -> None:
        """Use the approved stage request, preserving its regulation mode."""
        if base.state not in (OperatingState.CHARGE, OperatingState.DISCHARGE):
            self.instrument.configure_setpoints(base)
            return
        if not base.power_enabled or base.power < 0:
            raise ValueError("SoP-controlled stages require an enabled power channel")
        if self._base is not None and self._base.state != base.state:
            self._low_count = 0
        if self._base is None:
            self._next_tick = time.monotonic() + 1.0
        self._base = base
        self._profile_point = profile_point
        self._apply()

    def checkpoint(self) -> None:
        if self._base is None or self._next_tick is None:
            return
        now = time.monotonic()
        if now < self._next_tick:
            return
        self._next_tick = now + 1.0
        self._apply(count_low=True)

    def _apply(self, *, count_low: bool = False) -> None:
        base = self._base
        assert base is not None
        reading, value, minimum = self._reading(base.state)
        if count_low:
            self._low_count = self._low_count + 1 if value < minimum else 0
            if self._low_count >= self.configuration.low_cycles:
                self._terminal("sop_below_minimum", reading)
        static_power = (self.safety_limits.charge_power
                        if base.state == OperatingState.CHARGE
                        else self.safety_limits.discharge_power)
        applied = replace(base, power=min(base.power, self.workflow_max_power_w,
                                          static_power, value),
                          power_ceiling_enforced=True)
        if applied != self._applied or self._profile_point is not None:
            if self.arm_lease_supervisor is not None:
                if self._profile_point is not None:
                    index, point_value, mode = self._profile_point
                    self.arm_lease_supervisor.apply_profile_point(
                        index=index, value=point_value, mode=mode,
                        effective_setpoints=applied,
                    )
                else:
                    self.arm_lease_supervisor.apply_sop_limit(base, applied)
            else:
                self.instrument.configure_setpoints(applied)
            observed = self.instrument.read_status().setpoints
            if (not observed.power_enabled or observed.power > applied.power + 0.01):
                raise RuntimeError("NHR power setpoint readback exceeds the SoP ceiling")
            self._profile_point = None
            self._applied = applied
            self._record("limit_applied", source=reading, requested_power_w=base.power,
                         approved_power_w=min(base.power, self.workflow_max_power_w,
                                              static_power), applied_power_w=applied.power,
                         low_cycles=self._low_count)
        self._current_limit = {
            "source_id": self.configuration.source_id,
            "direction": base.state.name.lower(), "stage": self._stage,
            "source_sequence": reading["sequence"],
            "source_age_s_at_application": reading["age_s"], "sop_w": value,
            "requested_w": base.power,
            "approved_w": min(base.power, self.workflow_max_power_w, static_power),
            "applied_w": applied.power, "low_cycles": self._low_count,
        }

    def snapshot(self) -> dict[str, Any]:
        return {"configured": True, "source_id": self.configuration.source_id,
                "control_rate_hz": 1.0, "fault": self.fault,
                "current_limit": self._current_limit, "events": list(self.events)}

    def runtime_limit(self) -> dict[str, Any] | None:
        return None if self._current_limit is None else dict(self._current_limit)

    def is_power_restricted(self) -> bool:
        limit = self._current_limit
        return bool(limit is not None and limit["applied_w"] < limit["requested_w"])
