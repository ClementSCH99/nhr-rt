"""Dependency-light localhost HTTP/SSE service for 32/64-bit interoperability."""

from __future__ import annotations

import argparse
import json
import queue
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .acquisition import AcquisitionCollector
from .backends.ivi import DEFAULT_DRIVER_DLL, IVIBackend
from .backends.simulator import SimulatedBackend
from .errors import NHRError, NHRStateError, NHRValidationError
from .instrument import NHR9300
from .interlocks import StaticInterlockProvider
from .routines import RoutineRunner, routine_from_mapping
from .types import OperatingState, SafetyLimits, Setpoints, to_jsonable


@dataclass(slots=True)
class ManagedInstrument:
    instrument: NHR9300
    collector: AcquisitionCollector
    runner: RoutineRunner


class InstrumentManager:
    def __init__(self, config: Mapping[str, Any]) -> None:
        self.instruments: dict[str, ManagedInstrument] = {}
        output_dir = Path(str(config.get("output_dir", "runs")))
        for item in config.get("instruments", []):
            instrument_id = str(item["id"])
            backend_name = str(item.get("backend", "ivi"))
            if backend_name == "simulator":
                backend = SimulatedBackend(instrument_id)
            elif backend_name == "ivi":
                backend = IVIBackend(
                    instrument_id,
                    resource_name=str(item["logical_name"]),
                    driver_dll=Path(
                        str(item.get("driver_dll", DEFAULT_DRIVER_DLL))
                    ),
                )
            else:
                raise NHRValidationError(f"Unknown backend: {backend_name}")
            interlock = StaticInterlockProvider(
                safe=bool(item.get("operator_supervised", backend_name == "simulator"))
            )
            instrument = NHR9300(
                instrument_id,
                backend,
                interlocks=[interlock],
            )
            collector = AcquisitionCollector(
                instrument,
                rate_hz=float(item.get("rate_hz", 5.0)),
                csv_path=output_dir / f"{instrument_id}.csv",
            )
            self.instruments[instrument_id] = ManagedInstrument(
                instrument, collector, RoutineRunner(instrument, collector)
            )

    def get(self, instrument_id: str) -> ManagedInstrument:
        try:
            return self.instruments[instrument_id]
        except KeyError as exc:
            raise NHRValidationError(f"Unknown instrument: {instrument_id}") from exc

    def inventory(self) -> list[dict[str, Any]]:
        result = []
        for instrument_id, managed in self.instruments.items():
            try:
                status = managed.instrument.read_status()
                result.append(to_jsonable(status))
            except Exception:
                result.append({"instrument_id": instrument_id, "connected": False})
        return result

    def close(self) -> None:
        for managed in self.instruments.values():
            if managed.runner.running:
                managed.runner.stop()
            managed.collector.stop()
            managed.instrument.close()


class NHRRequestHandler(BaseHTTPRequestHandler):
    manager: InstrumentManager
    server_version = "NHR9300Service/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise NHRValidationError("JSON body must be an object")
        return parsed

    def _send(self, status: int, payload: Any) -> None:
        encoded = json.dumps(to_jsonable(payload)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _route(self) -> tuple[str | None, str | None]:
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        if len(parts) == 1 and parts[0] == "instruments":
            return None, "inventory"
        if len(parts) >= 2 and parts[0] == "instruments":
            return parts[1], parts[2] if len(parts) > 2 else "status"
        return None, None

    def do_GET(self) -> None:
        try:
            instrument_id, action = self._route()
            if action == "inventory":
                self._send(HTTPStatus.OK, self.manager.inventory())
                return
            if instrument_id is None:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            managed = self.manager.get(instrument_id)
            if action == "status":
                self._send(HTTPStatus.OK, managed.instrument.read_status())
            elif action == "measurement":
                sample = managed.collector.latest
                measurement = (
                    sample.measurement
                    if sample is not None
                    else managed.instrument.read_measurement()
                )
                self._send(HTTPStatus.OK, measurement)
            elif action == "routine":
                self._send(
                    HTTPStatus.OK,
                    managed.runner.result
                    or {"state": "idle", "instrument_id": instrument_id},
                )
            elif action == "stream":
                self._stream(managed)
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except Exception as exc:
            self._error(exc)

    def _stream(self, managed: ManagedInstrument) -> None:
        if not managed.collector.running:
            managed.collector.start()
        subscriber = managed.collector.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        while managed.collector.running:
            try:
                sample = subscriber.get(timeout=10.0)
                data = json.dumps(to_jsonable(sample.measurement))
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            except queue.Empty:
                self.wfile.write(b": keep-alive\n\n")
            except (BrokenPipeError, ConnectionResetError):
                return
            self.wfile.flush()

    def do_POST(self) -> None:
        try:
            instrument_id, action = self._route()
            if instrument_id is None:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            managed = self.manager.get(instrument_id)
            body = self._json_body()
            if action == "connect":
                managed.instrument.connect()
                managed.collector.start()
                payload: Any = managed.instrument.read_status()
            elif action == "disconnect":
                if managed.runner.running:
                    raise NHRStateError("Stop the active routine before disconnecting")
                managed.collector.stop()
                managed.instrument.close()
                payload = {"connected": False}
            elif action == "limits":
                managed.instrument.configure_safety_limits(SafetyLimits(**body))
                payload = {"configured": True}
            elif action == "arm":
                payload = {
                    "armed_until_monotonic": managed.instrument.arm(
                        float(body.get("duration_s", 30.0))
                    )
                }
            elif action == "command":
                payload = self._command(managed, body)
            elif action == "routine":
                result = managed.runner.start(routine_from_mapping(body))
                payload = result
            elif action == "stop":
                managed.runner.stop()
                payload = {"stop_requested": True}
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            self._send(HTTPStatus.OK, payload)
        except Exception as exc:
            self._error(exc)

    def _command(
        self, managed: ManagedInstrument, body: Mapping[str, Any]
    ) -> Any:
        command = str(body.get("name", ""))
        instrument = managed.instrument
        if command == "enable":
            instrument.enable()
        elif command == "standby":
            instrument.standby()
        elif command == "disable":
            instrument.disable()
        elif command == "emergency_stop":
            instrument.emergency_stop(str(body.get("reason", "API request")))
        elif command == "setpoints":
            values = dict(body.get("setpoints", {}))
            values["state"] = OperatingState[str(values["state"]).upper()]
            instrument.configure_setpoints(Setpoints(**values))
        else:
            raise NHRValidationError(f"Unknown command: {command}")
        return instrument.read_status()

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, (NHRError, ValueError, KeyError, TypeError)):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._send(status, {"error": str(exc), "type": type(exc).__name__})


def build_server(
    config: Mapping[str, Any], host: str = "127.0.0.1", port: int = 9300
) -> tuple[ThreadingHTTPServer, InstrumentManager]:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise NHRValidationError("The v1 service may bind to localhost only")
    manager = InstrumentManager(config)
    handler = type("ConfiguredNHRHandler", (NHRRequestHandler,), {"manager": manager})
    server = ThreadingHTTPServer((host, port), handler)
    return server, manager


def serve(config: Mapping[str, Any], host: str = "127.0.0.1", port: int = 9300) -> None:
    server, manager = build_server(config, host, port)
    try:
        server.serve_forever()
    finally:
        manager.close()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local NHR9300 service")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9300, type=int)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    serve(config, args.host, args.port)


if __name__ == "__main__":
    main()
