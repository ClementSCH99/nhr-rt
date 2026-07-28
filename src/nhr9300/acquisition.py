"""Continuous 1-10 Hz acquisition, in-memory publication and CSV logging."""

from __future__ import annotations

import csv
import queue
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .errors import NHRValidationError
from .instrument import NHR9300
from .types import InstrumentStatus, Measurement


@dataclass(frozen=True, slots=True)
class AcquisitionSample:
    measurement: Measurement
    routine_id: str = ""
    step: str = ""
    interlocks: str = "ok"
    error: str = ""


@dataclass(frozen=True, slots=True)
class AcquisitionStatistics:
    """Observed timing quality for a completed or running acquisition."""

    requested_rate_hz: float
    sample_count: int
    elapsed_s: float
    effective_rate_hz: float
    mean_interval_s: float | None
    max_interval_s: float | None
    overrun_count: int
    error: str | None


@dataclass(frozen=True, slots=True)
class AcquisitionState:
    requested_rate_hz: float
    active: bool
    sample_count: int
    first_sample_at: datetime | None
    observed_rate_hz: float
    csv_path: str | None
    last_error: str | None


class AcquisitionCollector:
    CSV_FIELDS = [
        "timestamp_utc",
        "monotonic_s",
        "instrument_id",
        "routine_id",
        "step",
        "state",
        "voltage_v",
        "current_a",
        "power_w",
        "energy_charge_kwh",
        "energy_discharge_kwh",
        "temperature_c",
        "setpoint_voltage_v",
        "setpoint_current_a",
        "setpoint_power_w",
        "interlocks",
        "error",
    ]

    def __init__(
        self,
        instrument: NHR9300,
        rate_hz: float = 5.0,
        csv_path: Path | None = None,
        status_refresh_interval_s: float = 1.0,
    ) -> None:
        if not 1.0 <= rate_hz <= 10.0:
            raise NHRValidationError("Acquisition rate must be between 1 and 10 Hz")
        if status_refresh_interval_s <= 0:
            raise NHRValidationError("Status refresh interval must be positive")
        self.instrument = instrument
        self.rate_hz = rate_hz
        self._csv_template = Path(csv_path) if csv_path else None
        self.csv_path = self._unique_csv_path() if self._csv_template else None
        self._has_started = False
        self.status_refresh_interval_s = status_refresh_interval_s
        self.latest: AcquisitionSample | None = None
        self._subscribers: list[queue.Queue[AcquisitionSample]] = []
        self._callbacks: list[Callable[[AcquisitionSample], None]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._context_lock = threading.Lock()
        self._routine_id = ""
        self._step = ""
        self._started_monotonic: float | None = None
        self._stopped_monotonic: float | None = None
        self._sample_times: list[float] = []
        self._first_sample_at: datetime | None = None
        self._overrun_count = 0
        self._statistics_lock = threading.Lock()
        self._cached_status: InstrumentStatus | None = None
        self._status_read_monotonic: float | None = None
        self.error: str | None = None

    def _unique_csv_path(self) -> Path:
        assert self._csv_template is not None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        template = self._csv_template
        candidate = template.with_name(f"{template.stem}_{stamp}{template.suffix}")
        index = 1
        while candidate.exists():
            candidate = template.with_name(
                f"{template.stem}_{stamp}_{index}{template.suffix}"
            )
            index += 1
        return candidate

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_context(self, routine_id: str = "", step: str = "") -> None:
        with self._context_lock:
            self._routine_id = routine_id
            self._step = step
        # A step transition may change state or setpoints; refresh on next row.
        self._cached_status = None

    def subscribe(self, maxsize: int = 100) -> queue.Queue[AcquisitionSample]:
        target: queue.Queue[AcquisitionSample] = queue.Queue(maxsize=maxsize)
        self._subscribers.append(target)
        return target

    def add_callback(self, callback: Callable[[AcquisitionSample], None]) -> None:
        self._callbacks.append(callback)

    def start(self) -> AcquisitionCollector:
        if self.running:
            return self
        if self._csv_template is not None and self._has_started:
            self.csv_path = self._unique_csv_path()
        self._has_started = True
        with self._statistics_lock:
            self._started_monotonic = time.monotonic()
            self._stopped_monotonic = None
            self._sample_times = []
            self._first_sample_at = None
            self._overrun_count = 0
            self.error = None
            self.latest = None
        self._cached_status = None
        self._status_read_monotonic = None
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"acquisition-{self.instrument.instrument_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def state(self) -> AcquisitionState:
        stats = self.statistics()
        with self._statistics_lock:
            first_sample = self._first_sample_at
        return AcquisitionState(
            requested_rate_hz=self.rate_hz,
            active=self.running,
            sample_count=stats.sample_count,
            first_sample_at=first_sample,
            observed_rate_hz=stats.effective_rate_hz,
            csv_path=str(self.csv_path) if self.csv_path is not None else None,
            last_error=stats.error,
        )

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise TimeoutError(
                    f"Acquisition for {self.instrument.instrument_id} did not stop "
                    f"within {timeout:.1f} s"
                )
            self._thread = None

    def statistics(self) -> AcquisitionStatistics:
        """Return timing evidence without touching the instrument."""
        with self._statistics_lock:
            started = self._started_monotonic
            stopped = self._stopped_monotonic
            sample_times = list(self._sample_times)
            overrun_count = self._overrun_count
            error = self.error
        if started is None:
            elapsed = 0.0
        else:
            elapsed = max(0.0, (stopped or time.monotonic()) - started)
        intervals = [
            current - previous
            for previous, current in zip(sample_times, sample_times[1:])
        ]
        observed_span = (
            sample_times[-1] - sample_times[0] if len(sample_times) >= 2 else 0.0
        )
        return AcquisitionStatistics(
            requested_rate_hz=self.rate_hz,
            sample_count=len(sample_times),
            elapsed_s=elapsed,
            effective_rate_hz=(
                (len(sample_times) - 1) / observed_span if observed_span else 0.0
            ),
            mean_interval_s=(statistics.fmean(intervals) if intervals else None),
            max_interval_s=(max(intervals) if intervals else None),
            overrun_count=overrun_count,
            error=error,
        )

    def _publish(self, sample: AcquisitionSample) -> None:
        self.latest = sample
        for target in list(self._subscribers):
            try:
                target.put_nowait(sample)
            except queue.Full:
                try:
                    target.get_nowait()
                    target.put_nowait(sample)
                except queue.Empty:
                    pass
        for callback in list(self._callbacks):
            callback(sample)

    def _row(self, sample: AcquisitionSample) -> dict[str, object]:
        measurement = sample.measurement
        now = time.monotonic()
        if (
            self._cached_status is None
            or self._status_read_monotonic is None
            or now - self._status_read_monotonic >= self.status_refresh_interval_s
        ):
            self._cached_status = self.instrument.read_status()
            self._status_read_monotonic = now
        status = self._cached_status
        return {
            "timestamp_utc": measurement.timestamp_utc.isoformat(),
            "monotonic_s": f"{measurement.monotonic_s:.6f}",
            "instrument_id": measurement.instrument_id,
            "routine_id": sample.routine_id,
            "step": sample.step,
            "state": status.state.name.lower(),
            "voltage_v": measurement.voltage_v,
            "current_a": measurement.current_a,
            "power_w": measurement.power_w,
            "energy_charge_kwh": measurement.energy_charge_kwh,
            "energy_discharge_kwh": measurement.energy_discharge_kwh,
            "temperature_c": measurement.temperature_c,
            "setpoint_voltage_v": status.setpoints.voltage,
            "setpoint_current_a": status.setpoints.current,
            "setpoint_power_w": status.setpoints.power,
            "interlocks": sample.interlocks,
            "error": sample.error,
        }

    def _run(self) -> None:
        period = 1.0 / self.rate_hz
        handle = None
        writer = None
        try:
            if self.csv_path is not None:
                self.csv_path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.csv_path.open("w", newline="", encoding="utf-8")
                writer = csv.DictWriter(handle, fieldnames=self.CSV_FIELDS)
                writer.writeheader()
            deadline = time.monotonic()
            while not self._stop.is_set():
                try:
                    measurement = self.instrument.read_measurement()
                    self.instrument.check_runtime_safety()
                    with self._context_lock:
                        sample = AcquisitionSample(
                            measurement, self._routine_id, self._step
                        )
                    self._publish(sample)
                    with self._statistics_lock:
                        if self._first_sample_at is None:
                            self._first_sample_at = measurement.timestamp_utc
                        self._sample_times.append(measurement.monotonic_s)
                    if writer is not None:
                        writer.writerow(self._row(sample))
                        handle.flush()
                except Exception as exc:
                    self.error = str(exc)
                    if self.instrument.may_be_energized:
                        try:
                            self.instrument.emergency_stop(
                                f"Acquisition/interlock failure: {exc}"
                            )
                        except Exception as stop_exc:
                            self.error = f"{self.error}; emergency stop: {stop_exc}"
                    break
                deadline += period
                if time.monotonic() > deadline:
                    with self._statistics_lock:
                        self._overrun_count += 1
                self._stop.wait(max(0.0, deadline - time.monotonic()))
        finally:
            with self._statistics_lock:
                self._stopped_monotonic = time.monotonic()
            if handle is not None:
                handle.close()
