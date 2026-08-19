"""Thread-confined, safety-checked public NHR9300 class."""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable, Sequence, TypeVar

from .backends.base import NHRBackend
from .errors import (
    NHRConnectionError,
    NHRInterlockError,
    NHRNotArmedError,
    NHRStateError,
    NHRValidationError,
)
from .interlocks import InterlockProvider, validate_interlocks
from .types import (
    Capabilities,
    Identity,
    InstrumentStatus,
    Measurement,
    OperatingState,
    SafetyLimits,
    SafetyLimitsReadback,
    Setpoints,
)

T = TypeVar("T")


class NHR9300:
    """Safe facade over an NHR backend.

    Backend access is serialized on a dedicated worker thread. Connecting never
    resets, disables, enables, or otherwise normalizes the observed hardware.
    """

    def __init__(
        self,
        instrument_id: str,
        backend: NHRBackend,
        *,
        interlocks: Sequence[InterlockProvider],
        interlock_max_age_s: float = 2.0,
        measurement_max_age_s: float = 2.0,
    ) -> None:
        self.instrument_id = instrument_id
        self._backend = backend
        self._interlocks = list(interlocks)
        self._interlock_max_age_s = interlock_max_age_s
        self._measurement_max_age_s = measurement_max_age_s
        self._requests: queue.Queue[
            tuple[Callable[..., Any] | None, tuple[Any, ...], Future[Any]]
        ] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._capabilities: Capabilities | None = None
        self._limits: SafetyLimits | None = None
        self._armed_until: float | None = None
        self._last_measurement: Measurement | None = None
        self._last_error: str | None = None
        self._may_be_energized = False
        self._lock = threading.RLock()

    def __enter__(self) -> NHR9300:
        return self.connect()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _worker(self) -> None:
        while True:
            function, args, future = self._requests.get()
            if function is None:
                future.set_result(None)
                return
            try:
                future.set_result(function(*args))
            except BaseException as exc:
                future.set_exception(exc)

    def _start_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._worker,
                name=f"nhr9300-{self.instrument_id}",
                daemon=True,
            )
            self._thread.start()

    def _call(self, function: Callable[..., T], *args: Any) -> T:
        if self._thread is None or not self._thread.is_alive():
            raise NHRConnectionError(f"{self.instrument_id} worker is not running")
        future: Future[T] = Future()
        self._requests.put((function, args, future))
        return future.result()

    def _require_connected(self) -> None:
        if not self._connected:
            raise NHRConnectionError(f"{self.instrument_id} is not connected")

    def connect(self) -> NHR9300:
        with self._lock:
            if self._connected:
                return self
            self._start_worker()
            self._call(self._backend.connect)
            self._connected = True
            try:
                self._capabilities = self._call(self._backend.read_capabilities)
                observed = self._call(self._backend.read_status)
                self._may_be_energized = observed.enabled and observed.state in (
                    OperatingState.CHARGE,
                    OperatingState.DISCHARGE,
                    OperatingState.BATTERY_EMULATION,
                )
            except Exception:
                self.close()
                raise
            return self

    def close(self) -> None:
        with self._lock:
            self._armed_until = None
            if self._thread is None:
                return
            close_error: BaseException | None = None
            if self._connected:
                try:
                    self._call(self._backend.close)
                except BaseException as exc:
                    close_error = exc
                finally:
                    self._connected = False
            future: Future[None] = Future()
            self._requests.put((None, (), future))
            future.result()
            self._thread.join(timeout=2.0)
            self._thread = None
            if close_error is not None:
                raise close_error

    def read_identity(self) -> Identity:
        self._require_connected()
        return self._call(self._backend.read_identity)

    def read_capabilities(self) -> Capabilities:
        self._require_connected()
        if self._capabilities is None:
            self._capabilities = self._call(self._backend.read_capabilities)
        return self._capabilities

    def read_status(self) -> InstrumentStatus:
        self._require_connected()
        raw = self._call(self._backend.read_status)
        return InstrumentStatus(
            instrument_id=raw.instrument_id,
            connected=raw.connected,
            remote=raw.remote,
            enabled=raw.enabled,
            state=raw.state,
            setpoints=raw.setpoints,
            armed_until_monotonic=self._armed_until,
            last_error=self._last_error,
        )

    def read_measurement(self) -> Measurement:
        self._require_connected()
        measurement = self._call(self._backend.read_measurement)
        self._last_measurement = measurement
        return measurement

    def _validate_limits(self, limits: SafetyLimits) -> None:
        if not limits.approved or not limits.profile_name.strip():
            raise NHRValidationError(
                "Safety limits require approved=True and a non-empty profile_name"
            )
        caps = self.read_capabilities()
        values = (
            limits.charge_current,
            limits.charge_voltage_max,
            limits.charge_power,
            limits.discharge_current,
            limits.discharge_voltage_min,
            limits.discharge_power,
        )
        if any(value < 0 for value in values):
            raise NHRValidationError("Safety limits cannot be negative")
        if not caps.voltage_min <= limits.discharge_voltage_min <= caps.voltage_max:
            raise NHRValidationError("Discharge voltage limit is outside capabilities")
        if not caps.voltage_min <= limits.charge_voltage_max <= caps.voltage_max:
            raise NHRValidationError("Charge voltage limit is outside capabilities")
        if limits.charge_current > caps.charge_current_max:
            raise NHRValidationError("Charge current limit exceeds capability")
        if limits.discharge_current > caps.discharge_current_max:
            raise NHRValidationError("Discharge current limit exceeds capability")
        if limits.charge_power > caps.charge_power_max:
            raise NHRValidationError("Charge power limit exceeds capability")
        if limits.discharge_power > caps.discharge_power_max:
            raise NHRValidationError("Discharge power limit exceeds capability")

    def configure_safety_limits(self, limits: SafetyLimits) -> None:
        self._require_connected()
        self._validate_limits(limits)
        self._call(self._backend.configure_safety_limits, limits)
        self._limits = limits
        self._armed_until = None

    def read_safety_limits(self) -> SafetyLimitsReadback:
        self._require_connected()
        return self._call(self._backend.read_safety_limits)

    def _validate_setpoints(self, setpoints: Setpoints) -> None:
        if self._limits is None:
            raise NHRValidationError("Configure approved safety limits first")
        if setpoints.control_mode not in {"current", "power"}:
            raise NHRValidationError("control_mode must be current or power")
        limits = self._limits
        if setpoints.current < 0 or setpoints.power < 0 or setpoints.voltage < 0:
            raise NHRValidationError("Setpoint magnitudes cannot be negative")
        if setpoints.state == OperatingState.CHARGE:
            if setpoints.current_enabled and setpoints.current > limits.charge_current:
                raise NHRValidationError("Charge current exceeds approved limit")
            if setpoints.power_enabled and setpoints.power > limits.charge_power:
                raise NHRValidationError("Charge power exceeds approved limit")
            if setpoints.voltage_enabled and setpoints.voltage > limits.charge_voltage_max:
                raise NHRValidationError("Charge voltage exceeds approved limit")
        elif setpoints.state == OperatingState.DISCHARGE:
            if setpoints.current_enabled and setpoints.current > limits.discharge_current:
                raise NHRValidationError("Discharge current exceeds approved limit")
            if setpoints.power_enabled and setpoints.power > limits.discharge_power:
                raise NHRValidationError("Discharge power exceeds approved limit")
            if (
                setpoints.voltage_enabled
                and setpoints.voltage < limits.discharge_voltage_min
            ):
                raise NHRValidationError("Discharge voltage is below approved limit")

    def configure_setpoints(self, setpoints: Setpoints) -> None:
        self._require_connected()
        self._validate_setpoints(setpoints)
        active_state = setpoints.state in (
            OperatingState.CHARGE,
            OperatingState.DISCHARGE,
            OperatingState.BATTERY_EMULATION,
        )
        if active_state:
            self._require_arm()
            self._require_fresh_measurement()
        self._call(self._backend.configure_setpoints, setpoints)
        status = self.read_status()
        self._may_be_energized = status.enabled and active_state

    def arm(self, duration_s: float = 30.0) -> float:
        self._require_connected()
        if self._limits is None:
            raise NHRStateError("Approved safety limits must be configured before arming")
        if not 1.0 <= duration_s <= 300.0:
            raise NHRValidationError("Arm duration must be between 1 and 300 seconds")
        status = self.read_status()
        if status.state not in (OperatingState.OFF, OperatingState.STANDBY):
            raise NHRStateError(f"Cannot arm from {status.state.name}")
        if status.enabled:
            raise NHRStateError(
                "Cannot arm while the module is enabled; explicitly disable it first"
            )
        validate_interlocks(self._interlocks, self._interlock_max_age_s)
        self._armed_until = time.monotonic() + duration_s
        return self._armed_until

    def renew_arm(self, duration_s: float = 30.0) -> float:
        """Extend an active software arm lease after fresh safety checks.

        This does not write to the backend. It exists for bounded long profiles
        whose control loop is still healthy and continuously observed.
        """
        self._require_connected()
        self._require_arm()
        self._require_fresh_measurement()
        if not 1.0 <= duration_s <= 300.0:
            raise NHRValidationError("Arm duration must be between 1 and 300 seconds")
        self._armed_until = time.monotonic() + duration_s
        return self._armed_until

    def _require_arm(self) -> None:
        if self._armed_until is None or time.monotonic() >= self._armed_until:
            self._armed_until = None
            raise NHRNotArmedError("No valid arm lease")
        validate_interlocks(self._interlocks, self._interlock_max_age_s)

    def check_interlocks(self) -> None:
        """Raise when any configured interlock is unsafe or stale."""
        self._require_connected()
        validate_interlocks(self._interlocks, self._interlock_max_age_s)

    @property
    def may_be_energized(self) -> bool:
        return self._may_be_energized

    def check_runtime_safety(self) -> None:
        """Validate the arm lease and interlocks only while energy may flow."""
        if self._may_be_energized:
            self._require_arm()

    def enable(self) -> None:
        self._require_connected()
        self._require_arm()
        self._require_fresh_measurement()
        self._call(self._backend.set_enabled, True)
        status = self.read_status()
        self._may_be_energized = status.state in (
            OperatingState.CHARGE,
            OperatingState.DISCHARGE,
            OperatingState.BATTERY_EMULATION,
        )

    def _require_fresh_measurement(self) -> None:
        if self._last_measurement is None:
            raise NHRStateError(
                "A fresh measurement is required before an active state"
            )
        if time.monotonic() - self._last_measurement.monotonic_s > self._measurement_max_age_s:
            raise NHRStateError("The latest measurement is stale")

    def standby(self) -> None:
        self._require_connected()
        self._call(self._backend.set_state, OperatingState.STANDBY)
        self._may_be_energized = False

    def disable(self) -> None:
        self._require_connected()
        try:
            self._call(self._backend.set_state, OperatingState.STANDBY)
        finally:
            self._call(self._backend.set_enabled, False)
            self._armed_until = None
            self._may_be_energized = False

    def emergency_stop(self, reason: str = "emergency stop requested") -> None:
        self._last_error = reason
        self._armed_until = None
        self._may_be_energized = False
        failures: list[Exception] = []
        for operation, argument in (
            (self._backend.set_state, OperatingState.STANDBY),
            (self._backend.set_enabled, False),
        ):
            try:
                self._call(operation, argument)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise NHRStateError(
                f"Emergency stop incomplete: {'; '.join(map(str, failures))}"
            ) from failures[-1]

    def set_watchdog(self, enabled: bool) -> None:
        """Explicit opt-in only; validate watchdog behavior on the bench first."""
        self._require_connected()
        self._call(self._backend.set_watchdog, enabled)

    def read_watchdog(self) -> bool:
        """Read the hardware value instead of trusting the last requested value."""
        self._require_connected()
        return self._call(self._backend.read_watchdog)
