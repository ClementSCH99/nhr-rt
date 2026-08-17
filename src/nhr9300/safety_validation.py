"""Small, observable checks used during Session 3 bench validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time
from typing import Callable

from .errors import NHRStateError, NHRValidationError
from .instrument import NHR9300
from .types import (
    InstrumentStatus,
    Measurement,
    OperatingState,
    SafetyLimits,
    SafetyLimitsReadback,
    Setpoints,
)


@dataclass(frozen=True, slots=True)
class PrimitiveResult:
    """Observed state around one isolated write."""

    name: str
    started_at_utc: datetime
    ended_at_utc: datetime
    status_before: InstrumentStatus
    status_after: InstrumentStatus


@dataclass(frozen=True, slots=True)
class PhaseBProfile:
    """Deliberately narrow profile for the first energized transition."""

    mode: OperatingState
    current_a: float
    voltage_v: float
    power_w: float
    duration_s: float
    arm_duration_s: float
    approved: bool
    profile_name: str


@dataclass(frozen=True, slots=True)
class PhaseBResult:
    initial_measurement: Measurement
    active_status: InstrumentStatus
    active_measurements: tuple[Measurement, ...]
    final_status: InstrumentStatus


def require_safe_start(status: InstrumentStatus) -> None:
    """Refuse validation if the bench is already enabled or in an active mode."""
    if status.enabled:
        raise NHRStateError(
            "Session 3 requires Enabled=False before its first write"
        )
    if status.state not in (OperatingState.OFF, OperatingState.STANDBY):
        raise NHRStateError(
            f"Session 3 requires OFF or STANDBY, observed {status.state.name}"
        )


def require_disabled_standby(status: InstrumentStatus) -> None:
    if status.enabled or status.state != OperatingState.STANDBY:
        raise NHRStateError(
            "Expected STANDBY with Enabled=False, "
            f"observed {status.state.name} with Enabled={status.enabled}"
        )


def require_disabled_inactive(status: InstrumentStatus) -> None:
    """Accept the two safe states observed after disabling NHR hardware."""
    if status.enabled or status.state not in (
        OperatingState.OFF,
        OperatingState.STANDBY,
    ):
        raise NHRStateError(
            "Expected OFF or STANDBY with Enabled=False, "
            f"observed {status.state.name} with Enabled={status.enabled}"
        )


def safety_limit_mismatches(
    requested: SafetyLimits,
    applied: SafetyLimitsReadback,
) -> list[str]:
    """Return readable differences between the profile and NHR readback."""
    comparisons = {
        "charge_current": (requested.charge_current, applied.charge_current),
        "charge_current_delay_s": (
            requested.current_delay_s,
            applied.charge_current_delay_s,
        ),
        "charge_voltage_max": (
            requested.charge_voltage_max,
            applied.charge_voltage_max,
        ),
        "charge_voltage_delay_s": (
            requested.voltage_delay_s,
            applied.charge_voltage_delay_s,
        ),
        "charge_power": (requested.charge_power, applied.charge_power),
        "charge_power_delay_s": (
            requested.power_delay_s,
            applied.charge_power_delay_s,
        ),
        "discharge_current": (
            requested.discharge_current,
            applied.discharge_current,
        ),
        "discharge_current_delay_s": (
            requested.current_delay_s,
            applied.discharge_current_delay_s,
        ),
        "discharge_voltage_min": (
            requested.discharge_voltage_min,
            applied.discharge_voltage_min,
        ),
        "discharge_voltage_delay_s": (
            requested.voltage_delay_s,
            applied.discharge_voltage_delay_s,
        ),
        "discharge_power": (
            requested.discharge_power,
            applied.discharge_power,
        ),
        "discharge_power_delay_s": (
            requested.power_delay_s,
            applied.discharge_power_delay_s,
        ),
    }
    if requested.uut_temperature_max is not None:
        comparisons["uut_temperature_max"] = (
            requested.uut_temperature_max,
            applied.uut_temperature_max,
        )

    mismatches: list[str] = []
    for name, (expected, observed) in comparisons.items():
        if observed is None or not math.isclose(
            expected, observed, rel_tol=1e-6, abs_tol=1e-4
        ):
            mismatches.append(
                f"{name}: requested={expected!r}, readback={observed!r}"
            )
    return mismatches


class SafetyPrimitiveValidator:
    """Run Session 3A writes one at a time and verify each observed result."""

    def __init__(self, instrument: NHR9300) -> None:
        self.instrument = instrument

    def _observe(
        self,
        name: str,
        operation: Callable[[], None],
        check: Callable[[InstrumentStatus], None],
    ) -> PrimitiveResult:
        before = self.instrument.read_status()
        started = datetime.now(timezone.utc)
        operation()
        after = self.instrument.read_status()
        ended = datetime.now(timezone.utc)
        check(after)
        return PrimitiveResult(name, started, ended, before, after)

    def run_phase_a(self, limits: SafetyLimits) -> list[PrimitiveResult]:
        """Validate only non-energizing primitives; never call enable()."""
        require_safe_start(self.instrument.read_status())
        results = [
            self._observe(
                "disable",
                self.instrument.disable,
                require_disabled_inactive,
            ),
            self._observe(
                "configure_limits",
                lambda: self.instrument.configure_safety_limits(limits),
                require_disabled_inactive,
            ),
            self._observe(
                "set_standby_channels_disabled",
                lambda: self.instrument.configure_setpoints(
                    Setpoints(state=OperatingState.STANDBY)
                ),
                self._require_standby_channels_disabled,
            ),
            self._observe(
                "standby",
                self.instrument.standby,
                self._require_standby_channels_disabled,
            ),
            self._observe(
                "final_disable",
                self.instrument.disable,
                require_disabled_inactive,
            ),
        ]
        return results

    @staticmethod
    def _require_standby_channels_disabled(status: InstrumentStatus) -> None:
        if status.state != OperatingState.STANDBY:
            raise NHRStateError(
                f"Expected STANDBY, observed {status.state.name}"
            )
        setpoints = status.setpoints
        if any(
            (
                setpoints.voltage_enabled,
                setpoints.current_enabled,
                setpoints.power_enabled,
                setpoints.resistance_enabled,
            )
        ):
            raise NHRStateError("A regulation channel remained enabled in STANDBY")


class LowSetpointValidator:
    """Execute one short, explicitly armed low-setpoint transition."""

    MAX_CURRENT_A = 1.0
    MAX_POWER_W = 100.0
    MAX_DURATION_S = 2.0

    def __init__(self, instrument: NHR9300) -> None:
        self.instrument = instrument

    def validate_profile(
        self,
        profile: PhaseBProfile,
        limits: SafetyLimits,
    ) -> None:
        if not profile.approved or not profile.profile_name.strip():
            raise NHRValidationError(
                "Phase 3B requires its own approved, named profile"
            )
        if profile.mode not in (
            OperatingState.CHARGE,
            OperatingState.DISCHARGE,
        ):
            raise NHRValidationError("Phase 3B mode must be CHARGE or DISCHARGE")
        if not 0.0 < profile.current_a <= self.MAX_CURRENT_A:
            raise NHRValidationError("Phase 3B current must be > 0 and <= 1 A")
        if not 0.0 < profile.power_w <= self.MAX_POWER_W:
            raise NHRValidationError("Phase 3B power must be > 0 and <= 100 W")
        if not 0.0 < profile.duration_s <= self.MAX_DURATION_S:
            raise NHRValidationError("Phase 3B duration must be > 0 and <= 2 s")
        if not 1.0 <= profile.arm_duration_s <= 30.0:
            raise NHRValidationError(
                "Phase 3B arm duration must be between 1 and 30 s"
            )
        if profile.arm_duration_s < profile.duration_s + 2.0:
            raise NHRValidationError(
                "Phase 3B arm duration requires at least 2 s of margin"
            )
        if not (
            limits.discharge_voltage_min
            <= profile.voltage_v
            <= limits.charge_voltage_max
        ):
            raise NHRValidationError(
                "Phase 3B voltage must remain inside the approved battery window"
            )
        mode_current_limit = (
            limits.charge_current
            if profile.mode == OperatingState.CHARGE
            else limits.discharge_current
        )
        mode_power_limit = (
            limits.charge_power
            if profile.mode == OperatingState.CHARGE
            else limits.discharge_power
        )
        if profile.current_a > mode_current_limit:
            raise NHRValidationError("Phase 3B current exceeds approved limits")
        if profile.power_w > mode_power_limit:
            raise NHRValidationError("Phase 3B power exceeds approved limits")

    def run(
        self,
        profile: PhaseBProfile,
        limits: SafetyLimits,
        *,
        poll_interval_s: float = 0.05,
    ) -> PhaseBResult:
        self.validate_profile(profile, limits)
        require_safe_start(self.instrument.read_status())
        samples: list[Measurement] = []
        initial: Measurement | None = None
        active_status: InstrumentStatus | None = None
        try:
            self.instrument.configure_safety_limits(limits)
            readback = self.instrument.read_safety_limits()
            mismatches = safety_limit_mismatches(limits, readback)
            if mismatches:
                raise NHRStateError(
                    "Safety-limit readback mismatch: " + "; ".join(mismatches)
                )

            self.instrument.arm(profile.arm_duration_s)
            initial = self.instrument.read_measurement()
            if not (
                limits.discharge_voltage_min
                <= initial.voltage_v
                <= limits.charge_voltage_max
            ):
                raise NHRStateError(
                    "Measured voltage is outside the approved battery window"
                )

            setpoints = Setpoints(
                state=profile.mode,
                voltage=profile.voltage_v,
                current=profile.current_a,
                power=profile.power_w,
                voltage_enabled=True,
                current_enabled=True,
                power_enabled=True,
            )
            # On real hardware SetState may energize immediately; there is no
            # separate enable() call in this validation.
            self.instrument.configure_setpoints(setpoints)
            active_status = self.instrument.read_status()
            self._require_active_status(active_status, profile.mode)
            deadline = time.monotonic() + profile.duration_s
            while time.monotonic() < deadline:
                self.instrument.check_runtime_safety()
                samples.append(self.instrument.read_measurement())
                time.sleep(
                    min(poll_interval_s, max(0.0, deadline - time.monotonic()))
                )
        finally:
            try:
                self.instrument.standby()
            finally:
                self.instrument.disable()

        final_status = self.instrument.read_status()
        require_disabled_inactive(final_status)
        if initial is None or active_status is None:
            raise NHRStateError("The active state was not observed")
        return PhaseBResult(
            initial_measurement=initial,
            active_status=active_status,
            active_measurements=tuple(samples),
            final_status=final_status,
        )

    @staticmethod
    def _require_active_status(
        status: InstrumentStatus,
        expected_mode: OperatingState,
    ) -> None:
        if not status.enabled or status.state != expected_mode:
            raise NHRStateError(
                f"Expected enabled {expected_mode.name}, observed "
                f"{status.state.name} with Enabled={status.enabled}"
            )
        setpoints = status.setpoints
        if not (
            setpoints.voltage_enabled
            and setpoints.current_enabled
            and setpoints.power_enabled
        ):
            raise NHRStateError(
                "Expected voltage, current and power channels enabled"
            )
