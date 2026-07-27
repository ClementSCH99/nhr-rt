"""Continuous 1-10 Hz acquisition, in-memory publication and CSV logging."""

from __future__ import annotations

import csv
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import NHRValidationError
from .instrument import NHR9300
from .types import Measurement


@dataclass(frozen=True, slots=True)
class AcquisitionSample:
    measurement: Measurement
    routine_id: str = ""
    step: str = ""
    interlocks: str = "ok"
    error: str = ""


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
    ) -> None:
        if not 1.0 <= rate_hz <= 10.0:
            raise NHRValidationError("Acquisition rate must be between 1 and 10 Hz")
        self.instrument = instrument
        self.rate_hz = rate_hz
        self.csv_path = Path(csv_path) if csv_path else None
        self.latest: AcquisitionSample | None = None
        self._subscribers: list[queue.Queue[AcquisitionSample]] = []
        self._callbacks: list[Callable[[AcquisitionSample], None]] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._context_lock = threading.Lock()
        self._routine_id = ""
        self._step = ""
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_context(self, routine_id: str = "", step: str = "") -> None:
        with self._context_lock:
            self._routine_id = routine_id
            self._step = step

    def subscribe(self, maxsize: int = 100) -> queue.Queue[AcquisitionSample]:
        target: queue.Queue[AcquisitionSample] = queue.Queue(maxsize=maxsize)
        self._subscribers.append(target)
        return target

    def add_callback(self, callback: Callable[[AcquisitionSample], None]) -> None:
        self._callbacks.append(callback)

    def start(self) -> AcquisitionCollector:
        if self.running:
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"acquisition-{self.instrument.instrument_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

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
        status = self.instrument.read_status()
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
                self._stop.wait(max(0.0, deadline - time.monotonic()))
        finally:
            if handle is not None:
                handle.close()
