"""Dependency-light localhost HTTP/SSE service for 32/64-bit interoperability."""

from __future__ import annotations

import argparse
import json
import logging
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

LOGGER = logging.getLogger(__name__)


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    """Local server that refuses to share its port with another NHR service."""

    allow_reuse_address = False


@dataclass(slots=True)
class ManagedInstrument:
    instrument: NHR9300
    collector: AcquisitionCollector
    runner: RoutineRunner
    backend_name: str


class InstrumentManager:
    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        config_path: Path | None = None,
        listen_url: str = "",
    ) -> None:
        self.instruments: dict[str, ManagedInstrument] = {}
        self.config_path = config_path
        self.listen_url = listen_url
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
                instrument,
                collector,
                RoutineRunner(instrument, collector),
                backend_name,
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

    def configuration(self) -> dict[str, Any]:
        return {
            "config_file": str(self.config_path) if self.config_path else None,
            "listen_url": self.listen_url,
            "restart_required_for_config_changes": True,
            "instruments": [
                {
                    "instrument_id": instrument_id,
                    "backend": managed.backend_name,
                    "requested_rate_hz": managed.collector.rate_hz,
                    "csv_path": (
                        str(managed.collector.csv_path)
                        if managed.collector.csv_path is not None
                        else None
                    ),
                }
                for instrument_id, managed in self.instruments.items()
            ],
        }

    def close(self) -> None:
        for managed in self.instruments.values():
            if managed.runner.running:
                managed.runner.stop()
            managed.collector.stop()
            managed.instrument.close()


class NHRRequestHandler(BaseHTTPRequestHandler):
    manager: InstrumentManager
    server_version = "NHR9300Service/0.1"
    stream_keepalive_interval_s = 10.0

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
        if len(parts) == 1 and parts[0] == "configuration":
            return None, "configuration"
        if len(parts) >= 2 and parts[0] == "instruments":
            return parts[1], parts[2] if len(parts) > 2 else "status"
        return None, None

    def do_GET(self) -> None:
        try:
            instrument_id, action = self._route()
            if action == "inventory":
                self._send(HTTPStatus.OK, self.manager.inventory())
                return
            if action == "configuration":
                self._send(HTTPStatus.OK, self.manager.configuration())
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
            elif action == "acquisition":
                self._send(HTTPStatus.OK, managed.collector.state())
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
        instrument_id = managed.instrument.instrument_id
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            while managed.collector.running:
                try:
                    sample = subscriber.get(
                        timeout=self.stream_keepalive_interval_s
                    )
                    data = json.dumps(to_jsonable(sample.measurement))
                    payload = f"data: {data}\n\n".encode("utf-8")
                except queue.Empty:
                    payload = b": keep-alive\n\n"
                self.wfile.write(payload)
                self.wfile.flush()
            if managed.collector.error:
                LOGGER.error(
                    "SSE stream ended because acquisition stopped: "
                    "instrument=%s error=%s csv=%s",
                    instrument_id,
                    managed.collector.error,
                    managed.collector.csv_path,
                )
            else:
                LOGGER.info(
                    "SSE stream ended after acquisition stopped: instrument=%s",
                    instrument_id,
                )
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            LOGGER.info(
                "SSE client disconnected: instrument=%s client=%s reason=%s",
                instrument_id,
                self.client_address,
                exc,
            )
        except OSError:
            LOGGER.exception(
                "Unexpected SSE transport error: instrument=%s client=%s",
                instrument_id,
                self.client_address,
            )
        except Exception:
            LOGGER.exception(
                "Unexpected SSE handler error: instrument=%s client=%s",
                instrument_id,
                self.client_address,
            )
        finally:
            managed.collector.unsubscribe(subscriber)
            # An SSE response cannot be reused for another HTTP request. Ensure
            # clients observe EOF when acquisition or the handler terminates.
            self.close_connection = True

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
    config: Mapping[str, Any],
    host: str = "127.0.0.1",
    port: int = 9300,
    *,
    config_path: Path | None = None,
    announce: bool = True,
) -> tuple[ThreadingHTTPServer, InstrumentManager]:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise NHRValidationError("The v1 service may bind to localhost only")
    manager = InstrumentManager(config, config_path=config_path)
    handler = type("ConfiguredNHRHandler", (NHRRequestHandler,), {"manager": manager})
    server = LocalThreadingHTTPServer((host, port), handler)
    bound_host, bound_port = server.server_address[:2]
    manager.listen_url = f"http://{bound_host}:{bound_port}"
    if announce:
        details = manager.configuration()
        print(f"NHR9300 config: {details['config_file'] or '<mapping>'}", flush=True)
        print(f"NHR9300 listening: {details['listen_url']}", flush=True)
        for item in details["instruments"]:
            print(
                "NHR9300 instrument: "
                f"{item['instrument_id']} backend={item['backend']} "
                f"rate={item['requested_rate_hz']:g} Hz "
                f"csv={item['csv_path']}",
                flush=True,
            )
    return server, manager


def serve(
    config: Mapping[str, Any],
    host: str = "127.0.0.1",
    port: int = 9300,
    *,
    config_path: Path | None = None,
) -> None:
    server, manager = build_server(config, host, port, config_path=config_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Service shutdown requested by operator (Ctrl+C)")
    finally:
        LOGGER.info("Closing NHR9300 service")
        manager.close()
        server.server_close()
        LOGGER.info("NHR9300 service stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Run the local NHR9300 service")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9300, type=int)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    serve(config, args.host, args.port, config_path=args.config.resolve())


if __name__ == "__main__":
    main()
