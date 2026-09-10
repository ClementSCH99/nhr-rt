"""Dependency-light localhost HTTP/SSE service for 32/64-bit interoperability."""

from __future__ import annotations

import argparse
import json
import logging
import queue
import threading
import time

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .acquisition import AcquisitionCollector
from .backends.ivi import DEFAULT_DRIVER_DLL, IVIBackend
from .backends.simulator import SimulatedBackend
from .errors import NHRError, NHRPolicyError, NHRStateError, NHRValidationError
from .evidence import check_output_directory
from .external_interlocks import ExternalInterlockManager
from .instrument import NHR9300
from .interlocks import StaticInterlockProvider
from .observability import (
    RUNTIME_SCHEMA_VERSION,
    RuntimeEventBroker,
    RuntimeEventPublisher,
)
from .routines import RoutineRunner, routine_from_mapping
from .types import (
    InstrumentStatus,
    OperatingState,
    SafetyLimits,
    Setpoints,
    to_jsonable,
)
from .workflow_registry import WorkflowRegistry
from .workflow_runs import WorkflowRunController

LOGGER = logging.getLogger(__name__)
MAX_JSON_BODY_BYTES = 256 * 1024


def _status_payload(status: Any) -> dict[str, Any]:
    """Serialize status with both stable numeric and beginner-friendly names."""
    payload = to_jsonable(status)
    try:
        payload["state_name"] = OperatingState(int(payload["state"])).name
    except (KeyError, TypeError, ValueError):
        payload["state_name"] = "UNKNOWN"
    if isinstance(payload.get("setpoints"), dict):
        try:
            payload["setpoints"]["state_name"] = OperatingState(
                int(payload["setpoints"]["state"])
            ).name
        except (KeyError, TypeError, ValueError):
            payload["setpoints"]["state_name"] = "UNKNOWN"
    return payload


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    """Local server that refuses to share its port with another NHR service."""

    allow_reuse_address = False


class EndpointClass(str, Enum):
    """Authority classes frozen by the v1 service contract."""

    READ_ONLY = "read_only"
    APPROVED_WORKFLOW_CONTROL = "approved_workflow_control"
    EXTERNAL_SNAPSHOT_CONTROL = "external_snapshot_control"
    PRIMITIVE_COMPATIBILITY_CONTROL = "primitive_compatibility_control"


READ_ONLY_ACTIONS = {
    "inventory",
    "configuration",
    "status",
    "measurement",
    "routine",
    "acquisition",
    "stream",
    "runtime",
    "events",
    "interlocks",
}
PRIMITIVE_ACTIONS = {
    "connect",
    "disconnect",
    "limits",
    "arm",
    "command",
    "routine",
    "stop",
}
PHYSICAL_PRIMITIVES_DISABLED_BY_DEFAULT = {
    "limits",
    "arm",
    "command",
    "routine",
    "stop",
}
APPROVED_WORKFLOW_ACTIONS = {
    "workflows",
    "workflow-preflight",
    "workflow-runs",
    "workflow-run",
    "workflow-stop",
}


def classify_endpoint(method: str, action: str) -> EndpointClass:
    """Return the authority class for an established v0.2.0 endpoint."""
    normalized_method = method.upper()
    if normalized_method == "GET" and action in READ_ONLY_ACTIONS:
        return EndpointClass.READ_ONLY
    if normalized_method == "POST" and action in PRIMITIVE_ACTIONS:
        return EndpointClass.PRIMITIVE_COMPATIBILITY_CONTROL
    if normalized_method == "PUT" and action == "external-snapshot":
        return EndpointClass.EXTERNAL_SNAPSHOT_CONTROL
    if action in APPROVED_WORKFLOW_ACTIONS:
        return EndpointClass.APPROVED_WORKFLOW_CONTROL
    raise NHRValidationError(
        f"Unclassified service endpoint: {normalized_method} {action}"
    )


@dataclass(slots=True)
class ManagedInstrument:
    instrument: NHR9300
    collector: AcquisitionCollector
    runner: RoutineRunner
    backend_name: str
    external_interlocks: ExternalInterlockManager
    primitive_compatibility_control: bool = False
    remote_workflow_control: bool = False
    controlled_stop_timeout_s: float | None = None
    simulator_initial_state_policy: str | None = None
    workflow_controller: WorkflowRunController | None = None
    event_broker: RuntimeEventBroker | None = None
    event_publisher: RuntimeEventPublisher | None = None
    shutdown_timeout_s: float = 3.0
    _lifecycle_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False
    )

    def ensure_running(self) -> Any:
        """Initialize the service-owned connection and acquisition once."""
        with self._lifecycle_lock:
            self.instrument.connect()
            self.collector.start()
            return self.instrument.read_status()

    def detach_observer(self) -> dict[str, bool]:
        """Acknowledge a legacy detach without changing shared resources."""
        with self._lifecycle_lock:
            return {
                "detached": True,
                "service_connected": self.instrument.connected,
            }

    def require_allowed(self, action: str) -> None:
        classification = classify_endpoint("POST", action)
        if classification != EndpointClass.PRIMITIVE_COMPATIBILITY_CONTROL:
            return
        if (
            self.workflow_controller is not None
            and self.workflow_controller.active
            and action in PHYSICAL_PRIMITIVES_DISABLED_BY_DEFAULT
        ):
            raise NHRStateError(
                "Primitive control is unavailable during an approved workflow"
            )
        if self.backend_name != "ivi":
            return
        if action not in PHYSICAL_PRIMITIVES_DISABLED_BY_DEFAULT:
            return
        if self.primitive_compatibility_control:
            return
        raise NHRPolicyError(
            "Primitive compatibility control is disabled for physical "
            f"instrument {self.instrument.instrument_id}: {action}"
        )

    def close(self) -> None:
        """Converge the service-owned runtime on a verified non-active state."""
        failures: list[str] = []
        if self.event_publisher is not None:
            try:
                self.event_publisher.close()
            except Exception as exc:
                failures.append(f"runtime event publisher stop failed: {exc}")
        if self.event_broker is not None:
            self.event_broker.close()
        if self.workflow_controller is not None:
            try:
                self.workflow_controller.close(self.shutdown_timeout_s)
            except Exception as exc:
                failures.append(f"approved workflow stop failed: {exc}")
            if self.workflow_controller.active:
                raise NHRStateError(
                    f"Shutdown incomplete for {self.instrument.instrument_id}: "
                    + "; ".join(failures)
                )
        with self._lifecycle_lock:
            if self.runner.running:
                self.runner.stop()
                if not self.runner.wait(self.shutdown_timeout_s):
                    failures.append(
                        "active routine did not stop within "
                        f"{self.shutdown_timeout_s:.1f} s"
                    )

            if self.instrument.connected:
                try:
                    self.instrument.disable()
                except Exception as exc:
                    failures.append(f"disable failed: {exc}")
                try:
                    self.instrument.set_watchdog(False)
                except Exception as exc:
                    failures.append(f"watchdog disable failed: {exc}")
                try:
                    status = self.instrument.read_status()
                    watchdog = self.instrument.read_watchdog()
                    if status.enabled or status.state not in (
                        OperatingState.OFF,
                        OperatingState.STANDBY,
                    ):
                        failures.append(
                            "final status is not disabled OFF/STANDBY"
                        )
                    if watchdog:
                        failures.append("watchdog remained enabled")
                except Exception as exc:
                    failures.append(f"final-state verification failed: {exc}")

            try:
                self.collector.stop(timeout=self.shutdown_timeout_s)
            except Exception as exc:
                failures.append(f"acquisition stop failed: {exc}")
            try:
                self.instrument.close()
            except Exception as exc:
                failures.append(f"instrument close failed: {exc}")

        if failures:
            raise NHRStateError(
                f"Shutdown incomplete for {self.instrument.instrument_id}: "
                + "; ".join(failures)
            )


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
        self.output_dir_diagnostic = check_output_directory(output_dir)
        backend_names: dict[str, str] = {}
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
            simulator_policy = None
            if backend_name == "simulator":
                simulator_policy = str(
                    item.get("simulator_initial_state_policy", "reset_from_profile")
                )
                if simulator_policy != "reset_from_profile":
                    raise NHRValidationError(
                        "simulator_initial_state_policy currently supports only "
                        "'reset_from_profile'"
                    )
            primitive_control = item.get(
                "primitive_compatibility_control", False
            )
            if not isinstance(primitive_control, bool):
                raise NHRValidationError(
                    "primitive_compatibility_control must be a boolean"
                )
            remote_workflow_control = item.get("remote_workflow_control", False)
            if not isinstance(remote_workflow_control, bool):
                raise NHRValidationError(
                    "remote_workflow_control must be a boolean"
                )
            raw_stop_policy = item.get("controlled_stop_policy")
            controlled_stop_timeout_s = None
            if raw_stop_policy is not None:
                if not isinstance(raw_stop_policy, Mapping):
                    raise NHRValidationError(
                        "controlled_stop_policy must be an object"
                    )
                approved = raw_stop_policy.get("approved", False)
                profile_name = str(raw_stop_policy.get("profile_name", ""))
                if not isinstance(approved, bool):
                    raise NHRValidationError(
                        "controlled_stop_policy approved must be boolean"
                    )
                if approved and profile_name.strip():
                    try:
                        controlled_stop_timeout_s = float(
                            raw_stop_policy.get("timeout_s")
                        )
                    except (TypeError, ValueError) as exc:
                        raise NHRValidationError(
                            "controlled stop timeout must be a number"
                        ) from exc
                    if controlled_stop_timeout_s <= 0:
                        raise NHRValidationError(
                            "controlled stop timeout must be greater than zero"
                        )
            interlock = StaticInterlockProvider(
                safe=bool(item.get("operator_supervised", backend_name == "simulator"))
            )
            external_interlocks = ExternalInterlockManager()
            instrument = NHR9300(
                instrument_id,
                backend,
                interlocks=[interlock, external_interlocks],
            )
            collector = AcquisitionCollector(
                instrument,
                rate_hz=float(item.get("rate_hz", 5.0)),
                csv_path=output_dir / f"{instrument_id}.csv",
            )
            self.instruments[instrument_id] = ManagedInstrument(
                instrument,
                collector,
                RoutineRunner(instrument, collector, manage_collector=False),
                backend_name,
                external_interlocks,
                primitive_control,
                remote_workflow_control,
                controlled_stop_timeout_s,
                simulator_policy,
            )
            backend_names[instrument_id] = backend_name
        registry = WorkflowRegistry.from_config(
            config,
            base_dir=(config_path.parent if config_path is not None else Path.cwd()),
            instrument_backends=backend_names,
        )
        for managed in self.instruments.values():
            managed.workflow_controller = WorkflowRunController(
                instrument=managed.instrument,
                collector=managed.collector,
                legacy_runner=managed.runner,
                registry=registry,
                output_dir=output_dir,
                hardware=managed.backend_name == "ivi",
                remote_enabled=managed.remote_workflow_control,
                operation_lock=managed._lifecycle_lock,
                controlled_stop_timeout_s=(
                    managed.controlled_stop_timeout_s
                    if managed.backend_name == "ivi"
                    else managed.shutdown_timeout_s
                ),
                reconnect_after_cleanup=True,
                simulator_initial_state_policy=(
                    managed.simulator_initial_state_policy
                ),
                external_interlocks=managed.external_interlocks,
            )
            managed.event_broker = RuntimeEventBroker(
                managed.instrument.instrument_id,
                subscriber_queue_size=int(config.get("event_queue_size", 100)),
            )
            event_publisher = RuntimeEventPublisher(
                lambda sample, target=managed: self._publish_runtime_updates(
                    target, sample
                )
            )
            managed.event_publisher = event_publisher
            managed.collector.add_callback(event_publisher.submit)
            managed.collector.add_error_callback(
                lambda _error, target=event_publisher: target.submit(None)
            )
            managed.collector.set_safety_failure_handler(
                managed.workflow_controller.handle_runtime_failure
            )

    @staticmethod
    def _publish_runtime_updates(managed: ManagedInstrument, sample: Any) -> None:
        broker = managed.event_broker
        if broker is None:
            return
        if sample is not None:
            broker.publish("measurement", to_jsonable(sample.measurement))
        if managed.workflow_controller is not None:
            broker.publish("workflow", managed.workflow_controller.runtime_snapshot())
        safety = managed.instrument.observability_state()
        broker.publish(
            "interlock",
            {
                "instrument_id": managed.instrument.instrument_id,
                "external_sources": managed.external_interlocks.status(),
                "results": safety["interlocks"],
            },
        )
        broker.publish(
            "limit",
            InstrumentManager._power_limit_snapshot(safety.get("safety_limits")),
        )
        alerts = InstrumentManager._alerts(managed, safety)
        if alerts:
            broker.publish("alert", {"active": alerts})

    @staticmethod
    def _power_limit_snapshot(limits: Any) -> dict[str, Any]:
        if limits is None:
            return {
                "configured": False,
                "charge_w": None,
                "discharge_w": None,
                "external_source": "not_configured",
            }
        return {
            "configured": True,
            "charge_w": limits.charge_power,
            "discharge_w": limits.discharge_power,
            "basis": "approved_static_safety_limits",
            "profile_name": limits.profile_name,
            "external_source": "not_configured",
        }

    @staticmethod
    def _alerts(
        managed: ManagedInstrument, safety: Mapping[str, Any]
    ) -> list[dict[str, str]]:
        alerts: list[dict[str, str]] = []
        if managed.collector.error:
            alerts.append(
                {
                    "code": "acquisition_error",
                    "severity": "error",
                    "message": managed.collector.error,
                }
            )
        if safety.get("last_error"):
            alerts.append(
                {
                    "code": "instrument_error",
                    "severity": "error",
                    "message": str(safety["last_error"]),
                }
            )
        for result in safety["interlocks"]:
            if not result["safe"] or not result["fresh"]:
                alerts.append(
                    {
                        "code": "interlock_unsafe_or_stale",
                        "severity": "error",
                        "message": result["name"],
                    }
                )
        return alerts

    def runtime_snapshot(self, instrument_id: str) -> dict[str, Any]:
        managed = self.get(instrument_id)
        cached_status = managed.instrument.cached_status()
        status = _status_payload(cached_status) if cached_status is not None else None
        if status is None:
            status = {
                "instrument_id": instrument_id,
                "connected": False,
                "remote": False,
                "enabled": False,
                "state": int(OperatingState.OFF),
                "state_name": OperatingState.OFF.name,
                "setpoints": None,
                "last_error": None,
            }
        sample = managed.collector.latest
        acquisition = to_jsonable(managed.collector.state())
        safety = managed.instrument.observability_state()
        workflow = (
            managed.workflow_controller.runtime_snapshot()
            if managed.workflow_controller is not None
            else {
                "active": False,
                "state": "idle",
                "progress": None,
                "progress_available": False,
            }
        )
        measurement = None
        measurement_age_s = None
        measurement_fresh = False
        if sample is not None:
            measurement = to_jsonable(sample.measurement)
            measurement_age_s = max(
                0.0, time.monotonic() - sample.measurement.monotonic_s
            )
            measurement_fresh = (
                measurement_age_s <= safety["measurement_max_age_s"]
            )
        totals = {
            "capacity_charge_ah": (
                None if measurement is None else measurement["capacity_charge_ah"]
            ),
            "capacity_discharge_ah": (
                None if measurement is None else measurement["capacity_discharge_ah"]
            ),
            "energy_charge_wh": (
                None
                if measurement is None or measurement["energy_charge_kwh"] is None
                else measurement["energy_charge_kwh"] * 1000.0
            ),
            "energy_discharge_wh": (
                None
                if measurement is None or measurement["energy_discharge_kwh"] is None
                else measurement["energy_discharge_kwh"] * 1000.0
            ),
        }
        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "generated_at_utc": datetime.now(timezone.utc),
            "instrument": {
                "instrument_id": instrument_id,
                "connected": status["connected"],
                "remote": status["remote"],
                "state": status["state"],
                "state_name": status["state_name"],
                "output_enabled": status["enabled"],
                "setpoints": status["setpoints"],
            },
            "measurement": {
                "available": measurement is not None,
                "fresh": measurement_fresh,
                "age_s": measurement_age_s,
                "max_age_s": safety["measurement_max_age_s"],
                "value": measurement,
            },
            "acquisition": {
                "active": acquisition["active"],
                "health": "error" if acquisition["last_error"] else "ok",
                "sample_count": acquisition["sample_count"],
                "requested_rate_hz": acquisition["requested_rate_hz"],
                "observed_rate_hz": acquisition["observed_rate_hz"],
                "evidence_path": acquisition["csv_path"],
                "last_error": acquisition["last_error"],
            },
            "workflow": workflow,
            "totals": totals,
            "external_sources": managed.external_interlocks.status(),
            "interlocks": {"results": safety["interlocks"]},
            "effective_power_limits": self._power_limit_snapshot(
                safety.get("safety_limits")
            ),
            "alerts": self._alerts(managed, safety),
        }

    def interlock_snapshot(self, instrument_id: str) -> dict[str, Any]:
        managed = self.get(instrument_id)
        safety = managed.instrument.observability_state()
        return {
            "instrument_id": instrument_id,
            "external_sources": managed.external_interlocks.status(),
            "results": safety["interlocks"],
        }

    def submit_external_snapshot(
        self, instrument_id: str, source_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        managed = self.get(instrument_id)
        snapshot = managed.external_interlocks.submit(source_id, body)
        if managed.event_broker is not None:
            managed.event_broker.publish(
                "interlock", self.interlock_snapshot(instrument_id)
            )
        return snapshot

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
                result.append(_status_payload(status))
            except Exception:
                result.append(
                    {
                        "instrument_id": instrument_id,
                        "connected": False,
                        "state": int(OperatingState.OFF),
                        "state_name": "UNKNOWN",
                    }
                )
        return result

    def configuration(self) -> dict[str, Any]:
        return {
            "config_file": str(self.config_path) if self.config_path else None,
            "listen_url": self.listen_url,
            "api_versions": ["v1"],
            "legacy_read_routes": True,
            "restart_required_for_config_changes": True,
            "output_dir_diagnostic": self.output_dir_diagnostic,
            "instruments": [
                {
                    "instrument_id": instrument_id,
                    "backend": managed.backend_name,
                    "simulator_initial_state_policy": (
                        managed.workflow_controller.simulator_initial_state_policy
                        if managed.workflow_controller is not None
                        else None
                    ),
                    "primitive_compatibility_control": (
                        managed.primitive_compatibility_control
                    ),
                    "remote_workflow_control": managed.remote_workflow_control,
                    "controlled_stop_timeout_configured": (
                        managed.controlled_stop_timeout_s is not None
                    ),
                    "requested_rate_hz": managed.collector.rate_hz,
                    "event_queue_size": (
                        managed.event_broker.subscriber_queue_size
                        if managed.event_broker is not None
                        else None
                    ),
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
        failures = []
        for managed in self.instruments.values():
            try:
                managed.close()
            except Exception as exc:
                LOGGER.exception(
                    "Instrument shutdown failed: instrument=%s",
                    managed.instrument.instrument_id,
                )
                failures.append(str(exc))
        if failures:
            raise NHRStateError("; ".join(failures))


class NHRRequestHandler(BaseHTTPRequestHandler):
    manager: InstrumentManager
    server_version = "NHR9300Service/0.2"
    stream_keepalive_interval_s = 10.0
    runtime_event_poll_interval_s = 0.25

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise NHRValidationError("Content-Length must be an integer") from exc
        if length < 0:
            raise NHRValidationError("Content-Length must be non-negative")
        if length > MAX_JSON_BODY_BYTES:
            raise NHRValidationError(
                f"JSON body exceeds the {MAX_JSON_BODY_BYTES}-byte limit"
            )
        if length == 0:
            return {}
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise NHRValidationError("JSON body must be an object")
        return parsed

    def _send(self, status: int, payload: Any) -> None:
        serialized = (
            _status_payload(payload)
            if isinstance(payload, InstrumentStatus)
            else to_jsonable(payload)
        )
        encoded = json.dumps(serialized).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    @staticmethod
    def _require_body_fields(
        body: Mapping[str, Any],
        *,
        allowed: set[str],
        required: set[str] = frozenset(),
    ) -> None:
        unknown = set(body) - allowed
        missing = required - set(body)
        if unknown:
            raise NHRValidationError(
                f"Unknown workflow request fields: {sorted(unknown)}"
            )
        if missing:
            raise NHRValidationError(
                f"Missing workflow request fields: {sorted(missing)}"
            )

    def _route(self) -> tuple[str | None, str | None, str | None]:
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        versioned = parts[:2] == ["api", "v1"]
        if versioned:
            parts = parts[2:]
        if len(parts) == 1 and parts[0] == "instruments":
            return None, "inventory", None
        if len(parts) == 1 and parts[0] == "configuration":
            return None, "configuration", None
        if len(parts) == 2 and parts[0] == "instruments":
            return parts[1], "status", None
        if len(parts) == 3 and parts[0] == "instruments":
            if parts[2] in {
                "workflows", "workflow-runs", "runtime", "events", "interlocks"
            }:
                if not versioned:
                    return None, None, None
                return parts[1], parts[2], None
            return parts[1], parts[2], None
        if (
            versioned
            and len(parts) == 4
            and parts[0] == "instruments"
            and parts[2] == "workflow-runs"
        ):
            if parts[3] == "preflight":
                return parts[1], "workflow-preflight", None
            return parts[1], "workflow-run", parts[3]
        if (
            versioned
            and len(parts) == 5
            and parts[0] == "instruments"
            and parts[2] == "workflow-runs"
            and parts[4] == "stop"
        ):
            return parts[1], "workflow-stop", parts[3]
        if (
            versioned
            and len(parts) == 5
            and parts[0] == "instruments"
            and parts[2] == "external-sources"
            and parts[4] == "snapshot"
        ):
            return parts[1], "external-snapshot", parts[3]
        return None, None, None

    def do_GET(self) -> None:
        try:
            instrument_id, action, run_id = self._route()
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
            if action not in READ_ONLY_ACTIONS | {"workflows", "workflow-run"}:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            classify_endpoint("GET", action)
            if action == "status":
                self._send(
                    HTTPStatus.OK, _status_payload(managed.instrument.read_status())
                )
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
            elif action == "runtime":
                self._send(
                    HTTPStatus.OK,
                    self.manager.runtime_snapshot(instrument_id),
                )
            elif action == "events":
                self._events(managed)
            elif action == "interlocks":
                self._send(
                    HTTPStatus.OK,
                    self.manager.interlock_snapshot(instrument_id),
                )
            elif action == "workflows":
                classify_endpoint("GET", action)
                assert managed.workflow_controller is not None
                self._send(HTTPStatus.OK, managed.workflow_controller.workflows())
            elif action == "workflow-run":
                classify_endpoint("GET", action)
                assert managed.workflow_controller is not None and run_id is not None
                self._send(HTTPStatus.OK, managed.workflow_controller.get(run_id))
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except Exception as exc:
            self._error(exc)

    def do_PUT(self) -> None:
        try:
            instrument_id, action, source_id = self._route()
            if instrument_id is None or action != "external-snapshot" or source_id is None:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            classify_endpoint("PUT", action)
            body = self._json_body()
            payload = self.manager.submit_external_snapshot(
                instrument_id, source_id, body
            )
            self._send(HTTPStatus.OK, payload)
        except Exception as exc:
            self._error(exc)

    def _events(self, managed: ManagedInstrument) -> None:
        broker = managed.event_broker
        if broker is None:
            raise NHRStateError("Runtime event broker is unavailable")
        subscriber = broker.subscribe()
        instrument_id = managed.instrument.instrument_id
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            initial = self.manager.runtime_snapshot(instrument_id)
            broker.publish("workflow", initial["workflow"])
            broker.publish(
                "interlock", self.manager.interlock_snapshot(instrument_id)
            )
            broker.publish("limit", initial["effective_power_limits"])
            if initial["alerts"]:
                broker.publish("alert", {"active": initial["alerts"]})
            while not broker.closed:
                try:
                    event = subscriber.get(
                        timeout=self.runtime_event_poll_interval_s
                    )
                    payload_data = to_jsonable(event)
                    dropped = broker.take_dropped_count(subscriber)
                    if dropped:
                        payload_data["dropped_before"] = dropped
                    data = json.dumps(payload_data)
                    payload = (
                        f"id: {event.sequence}\n"
                        f"event: {event.event}\n"
                        f"data: {data}\n\n"
                    ).encode("utf-8")
                except queue.Empty:
                    payload = b": keep-alive\n\n"
                self.wfile.write(payload)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            LOGGER.info(
                "Runtime SSE client disconnected: instrument=%s client=%s reason=%s",
                instrument_id,
                self.client_address,
                exc,
            )
        except OSError:
            LOGGER.exception(
                "Unexpected runtime SSE transport error: instrument=%s client=%s",
                instrument_id,
                self.client_address,
            )
        except Exception:
            LOGGER.exception(
                "Unexpected runtime SSE handler error: instrument=%s client=%s",
                instrument_id,
                self.client_address,
            )
        finally:
            broker.unsubscribe(subscriber)
            self.close_connection = True

    def _stream(self, managed: ManagedInstrument) -> None:
        managed.ensure_running()
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
            instrument_id, action, run_id = self._route()
            if instrument_id is None:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            managed = self.manager.get(instrument_id)
            if action not in PRIMITIVE_ACTIONS | {
                "workflow-preflight",
                "workflow-runs",
                "workflow-stop",
            }:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            body = self._json_body()
            if action in APPROVED_WORKFLOW_ACTIONS:
                classify_endpoint("POST", action)
                assert managed.workflow_controller is not None
                if action == "workflow-preflight":
                    self._require_body_fields(
                        body,
                        allowed={"workflow_id", "bundle_digest"},
                        required={"workflow_id", "bundle_digest"},
                    )
                    payload = managed.workflow_controller.preflight(
                        str(body.get("workflow_id", "")),
                        str(body.get("bundle_digest", "")),
                    )
                    self._send(HTTPStatus.OK, payload)
                    return
                if action == "workflow-runs":
                    self._require_body_fields(
                        body,
                        allowed={
                            "request_id",
                            "workflow_id",
                            "bundle_digest",
                            "operator_acknowledgement",
                        },
                        required={"request_id", "workflow_id", "bundle_digest"},
                    )
                    payload, created = managed.workflow_controller.start(
                        request_id=str(body.get("request_id", "")),
                        workflow_id=str(body.get("workflow_id", "")),
                        bundle_digest=str(body.get("bundle_digest", "")),
                        operator_acknowledgement=str(
                            body.get("operator_acknowledgement", "")
                        ),
                    )
                    self._send(
                        HTTPStatus.ACCEPTED if created else HTTPStatus.OK,
                        payload,
                    )
                    return
                if action == "workflow-stop":
                    self._require_body_fields(body, allowed=set())
                    if run_id is None:
                        raise NHRValidationError("run_id is required")
                    payload, requested = managed.workflow_controller.stop(run_id)
                    payload["stop_accepted"] = requested
                    payload["already_requested"] = not requested
                    self._send(
                        HTTPStatus.ACCEPTED if requested else HTTPStatus.OK,
                        payload,
                    )
                    return
            managed.require_allowed(action)
            with managed._lifecycle_lock:
                if action == "connect":
                    payload = managed.ensure_running()
                elif action == "disconnect":
                    payload = managed.detach_observer()
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
        if isinstance(exc, NHRPolicyError):
            status = HTTPStatus.FORBIDDEN
        elif isinstance(exc, NHRStateError):
            status = HTTPStatus.CONFLICT
        elif isinstance(exc, (NHRError, ValueError, KeyError, TypeError)):
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
        try:
            manager.close()
        finally:
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
