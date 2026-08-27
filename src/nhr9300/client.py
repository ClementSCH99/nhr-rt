"""Pure-standard-library client usable from a 64-bit Python process."""

from __future__ import annotations

import json
import threading
from typing import Any, Iterator, Mapping, TypedDict
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .errors import NHRError


class WorkflowSummary(TypedDict, total=False):
    workflow_id: str
    instrument_id: str
    bundle_digest: str | None
    available: bool
    error: str | None
    profile_name: str | None
    test_description: str | None
    stage_count: int


class WorkflowRunSnapshot(TypedDict, total=False):
    run_id: str
    request_id: str
    workflow_id: str
    bundle_digest: str
    instrument_id: str
    state: str
    outcome: str | None
    stop_requested: bool
    report_path: str
    error: str | None


class RuntimeSnapshot(TypedDict, total=False):
    schema_version: str
    generated_at_utc: str
    instrument: dict[str, Any]
    measurement: dict[str, Any]
    acquisition: dict[str, Any]
    workflow: dict[str, Any]
    totals: dict[str, float | None]
    external_sources: dict[str, Any]
    interlocks: dict[str, Any]
    effective_power_limits: dict[str, Any]
    alerts: list[dict[str, str]]


class NHRServiceClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9300",
        *,
        api_version: str | None = "v1",
    ) -> None:
        if api_version not in (None, "v1"):
            raise ValueError("api_version must be 'v1' or None")
        root = base_url.rstrip("/")
        suffix = f"/api/{api_version}" if api_version else ""
        self.base_url = root if root.endswith(suffix) else root + suffix

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout_s: float = 10.0,
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            payload = json.loads(exc.read().decode("utf-8"))
            raise NHRError(payload.get("error", str(exc))) from exc

    def instruments(self) -> list[dict[str, Any]]:
        return self._request("GET", "/instruments")

    def configuration(self) -> dict[str, Any]:
        return self._request("GET", "/configuration")

    def connect(self, instrument_id: str) -> dict[str, Any]:
        return self._request("POST", f"/instruments/{instrument_id}/connect", {})

    def disconnect(self, instrument_id: str) -> dict[str, Any]:
        return self._request("POST", f"/instruments/{instrument_id}/disconnect", {})

    def status(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}")

    def measurement(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}/measurement")

    def acquisition(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}/acquisition")

    def runtime(self, instrument_id: str) -> RuntimeSnapshot:
        return self._request("GET", f"/instruments/{instrument_id}/runtime")

    def configure_limits(
        self, instrument_id: str, limits: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._request("POST", f"/instruments/{instrument_id}/limits", limits)

    def arm(self, instrument_id: str, duration_s: float = 30.0) -> dict[str, Any]:
        return self._request(
            "POST", f"/instruments/{instrument_id}/arm", {"duration_s": duration_s}
        )

    def command(
        self, instrument_id: str, name: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/instruments/{instrument_id}/command",
            {"name": name, **kwargs},
        )

    def start_routine(
        self, instrument_id: str, definition: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/instruments/{instrument_id}/routine", definition
        )

    def routine(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}/routine")

    def stop(self, instrument_id: str) -> dict[str, Any]:
        return self._request("POST", f"/instruments/{instrument_id}/stop", {})

    def workflows(self, instrument_id: str) -> list[WorkflowSummary]:
        return self._request("GET", f"/instruments/{instrument_id}/workflows")

    def preflight_workflow(
        self,
        instrument_id: str,
        workflow_id: str,
        bundle_digest: str,
        *,
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/instruments/{instrument_id}/workflow-runs/preflight",
            {"workflow_id": workflow_id, "bundle_digest": bundle_digest},
            timeout_s=timeout_s,
        )

    def start_workflow(
        self,
        instrument_id: str,
        *,
        request_id: str,
        workflow_id: str,
        bundle_digest: str,
        operator_acknowledgement: str = "",
    ) -> WorkflowRunSnapshot:
        return self._request(
            "POST",
            f"/instruments/{instrument_id}/workflow-runs",
            {
                "request_id": request_id,
                "workflow_id": workflow_id,
                "bundle_digest": bundle_digest,
                "operator_acknowledgement": operator_acknowledgement,
            },
        )

    def workflow_run(
        self, instrument_id: str, run_id: str
    ) -> WorkflowRunSnapshot:
        return self._request(
            "GET", f"/instruments/{instrument_id}/workflow-runs/{run_id}"
        )

    def stop_workflow(
        self, instrument_id: str, run_id: str
    ) -> WorkflowRunSnapshot:
        return self._request(
            "POST",
            f"/instruments/{instrument_id}/workflow-runs/{run_id}/stop",
            {},
        )

    def stream(
        self,
        instrument_id: str,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        if stop_event is not None and stop_event.is_set():
            return
        request = Request(
            self.base_url + f"/instruments/{instrument_id}/stream",
            headers={"Accept": "text/event-stream"},
        )
        with urlopen(request, timeout=None) as response:
            for raw_line in response:
                if stop_event is not None and stop_event.is_set():
                    return
                line = raw_line.decode("utf-8").strip()
                if line.startswith("data: "):
                    yield json.loads(line[6:])

    def events(
        self,
        instrument_id: str,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield typed v1 runtime SSE event envelopes."""
        if stop_event is not None and stop_event.is_set():
            return
        request = Request(
            self.base_url + f"/instruments/{instrument_id}/events",
            headers={"Accept": "text/event-stream"},
        )
        with urlopen(request, timeout=None) as response:
            event_name: str | None = None
            for raw_line in response:
                if stop_event is not None and stop_event.is_set():
                    return
                line = raw_line.decode("utf-8").strip()
                if line.startswith("event: "):
                    event_name = line[7:]
                elif line.startswith("data: "):
                    payload = json.loads(line[6:])
                    if event_name is not None and payload.get("event") != event_name:
                        raise NHRError("Runtime SSE event name does not match payload")
                    yield payload
                    event_name = None
