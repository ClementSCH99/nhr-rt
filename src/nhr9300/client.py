"""Pure-standard-library client usable from a 64-bit Python process."""

from __future__ import annotations

import json
import queue
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, TypedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import (
    NHRAPIError,
    NHRPendingSnapshotError,
    NHRProtocolError,
    NHRTransportError,
    NHRWorkflowTimeout,
)


TERMINAL_WORKFLOW_STATES = frozenset(
    {"passed", "stopped", "failed", "interrupted"}
)


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


class ServiceConfiguration(TypedDict, total=False):
    api_versions: list[str]
    contracts: dict[str, str]
    capabilities: list[str]
    restart_required_for_config_changes: bool
    instruments: list[dict[str, Any]]


class ExternalSnapshotReceipt(TypedDict, total=False):
    source_id: str
    sequence: int
    timestamp_utc: str
    received_at_utc: str
    health: str
    signals: dict[str, float | bool]


class InterlockSnapshot(TypedDict, total=False):
    instrument_id: str
    external_sources: dict[str, Any]
    results: list[dict[str, Any]]


class RuntimeEventEnvelope(TypedDict, total=False):
    schema_version: str
    sequence: int
    timestamp_utc: str
    instrument_id: str
    event: str
    data: dict[str, Any]
    dropped_before: int


class NHRServiceClient:
    """Dependency-free client for the localhost NHR service.

    The client is only a requester and observer. Closing it, losing transport,
    or timing out does not stop a service-owned workflow and does not establish
    physical safe state.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9300",
        *,
        api_version: str | None = "v1",
    ) -> None:
        """Create a client without connecting to the service.

        ``api_version=None`` selects the legacy unversioned read routes.
        """
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
                raw = response.read()
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as parse_exc:
                raise NHRProtocolError(
                    f"HTTP {exc.code} contained an invalid JSON error response"
                ) from parse_exc
            raise NHRAPIError(
                str(payload.get("error", exc.reason)),
                status=exc.code,
                error_type=payload.get("type"),
                code=payload.get("code"),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise NHRTransportError(
                "NHR service transport failed; instrument and workflow state "
                f"are unknown: {exc}"
            ) from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NHRProtocolError("NHR service returned invalid JSON") from exc

    def instruments(self) -> list[dict[str, Any]]:
        """List configured instruments without connecting them."""
        return self._request("GET", "/instruments")

    def configuration(self) -> ServiceConfiguration:
        """Return resolved public service configuration and feature flags."""
        return self._request("GET", "/configuration")

    def connect(self, instrument_id: str) -> dict[str, Any]:
        """Ask the service to initialize its shared instrument runtime."""
        return self._request("POST", f"/instruments/{instrument_id}/connect", {})

    def detach_observer(self, instrument_id: str) -> dict[str, Any]:
        """Detach this compatibility observer without closing shared resources."""
        return self._request("POST", f"/instruments/{instrument_id}/disconnect", {})

    def disconnect(self, instrument_id: str) -> dict[str, Any]:
        """Deprecated alias for :meth:`detach_observer`.

        It does not disconnect the physical instrument or stop acquisition.
        """
        warnings.warn(
            "disconnect() only detaches an observer; use detach_observer()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.detach_observer(instrument_id)

    def status(self, instrument_id: str) -> dict[str, Any]:
        """Read instrument status; the service must already own a connection."""
        return self._request("GET", f"/instruments/{instrument_id}")

    def measurement(self, instrument_id: str) -> dict[str, Any]:
        return self._request("GET", f"/instruments/{instrument_id}/measurement")

    def acquisition(self, instrument_id: str) -> dict[str, Any]:
        """Return service acquisition health, sample count and current CSV path."""
        return self._request("GET", f"/instruments/{instrument_id}/acquisition")

    def runtime(self, instrument_id: str) -> RuntimeSnapshot:
        """Return one read-only consolidated snapshot for UI or recovery logic."""
        return self._request("GET", f"/instruments/{instrument_id}/runtime")

    def interlocks(self, instrument_id: str) -> InterlockSnapshot:
        """Return current static and external interlock decisions."""
        return self._request("GET", f"/instruments/{instrument_id}/interlocks")

    def submit_external_snapshot(
        self,
        instrument_id: str,
        source_id: str,
        *,
        sequence: int,
        timestamp_utc: str,
        health: str,
        signals: dict[str, float | bool],
        timeout_s: float = 10.0,
    ) -> ExternalSnapshotReceipt:
        """Forward decoded data with a caller-bounded transport timeout.

        If the response is lost, retry the exact same snapshot and sequence.
        The service accepts that retry idempotently.
        """
        return self._request(
            "PUT",
            f"/instruments/{instrument_id}/external-sources/{source_id}/snapshot",
            {
                "sequence": sequence,
                "timestamp_utc": timestamp_utc,
                "health": health,
                "signals": signals,
            },
            timeout_s=timeout_s,
        )

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
        """Deprecated alias for stopping the legacy primitive routine API."""
        warnings.warn(
            "stop() targets the legacy routine API; use stop_legacy_routine()",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.stop_legacy_routine(instrument_id)

    def stop_legacy_routine(self, instrument_id: str) -> dict[str, Any]:
        """Request stop of the legacy primitive routine, not a workflow run."""
        return self._request("POST", f"/instruments/{instrument_id}/stop", {})

    def workflows(self, instrument_id: str) -> list[WorkflowSummary]:
        """List startup-registered workflows and their availability/digests."""
        return self._request("GET", f"/instruments/{instrument_id}/workflows")

    def preflight_workflow(
        self,
        instrument_id: str,
        workflow_id: str,
        bundle_digest: str,
        *,
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Synchronously validate a registered workflow without running stages."""
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
        """Start one approved service-owned workflow asynchronously.

        ``request_id`` is an idempotency key: retrying the same request returns
        the existing run instead of creating a second one.
        """
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
        """Return the latest snapshot for a known service workflow run."""
        return self._request(
            "GET", f"/instruments/{instrument_id}/workflow-runs/{run_id}"
        )

    def stop_workflow(
        self, instrument_id: str, run_id: str
    ) -> WorkflowRunSnapshot:
        """Request cooperative workflow stop; poll or use a wait helper afterward."""
        return self._request(
            "POST",
            f"/instruments/{instrument_id}/workflow-runs/{run_id}/stop",
            {},
        )

    def wait_workflow(
        self,
        instrument_id: str,
        run_id: str,
        *,
        timeout_s: float = 60.0,
        poll_interval_s: float = 0.5,
        progress_callback: Callable[[WorkflowRunSnapshot], None] | None = None,
    ) -> WorkflowRunSnapshot:
        """Poll until a workflow is terminal, without stopping it on timeout."""
        if timeout_s <= 0 or poll_interval_s <= 0:
            raise ValueError("timeout_s and poll_interval_s must be greater than zero")
        deadline = time.monotonic() + timeout_s
        while True:
            snapshot = self.workflow_run(instrument_id, run_id)
            if progress_callback is not None:
                progress_callback(snapshot)
            if snapshot.get("state") in TERMINAL_WORKFLOW_STATES:
                return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NHRWorkflowTimeout(
                    f"Workflow {run_id} did not become terminal within "
                    f"{timeout_s:g} s; no stop request was sent"
                )
            time.sleep(min(poll_interval_s, remaining))

    def stop_and_wait_workflow(
        self,
        instrument_id: str,
        run_id: str,
        *,
        timeout_s: float = 60.0,
        poll_interval_s: float = 0.5,
        progress_callback: Callable[[WorkflowRunSnapshot], None] | None = None,
    ) -> WorkflowRunSnapshot:
        """Request cooperative stop, then wait for a terminal run snapshot."""
        self.stop_workflow(instrument_id, run_id)
        return self.wait_workflow(
            instrument_id,
            run_id,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            progress_callback=progress_callback,
        )

    def stream(
        self,
        instrument_id: str,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield legacy measurement SSE messages; iteration blocks for data."""
        if stop_event is not None and stop_event.is_set():
            return
        request = Request(
            self.base_url + f"/instruments/{instrument_id}/stream",
            headers={"Accept": "text/event-stream"},
        )
        try:
            with urlopen(request, timeout=None) as response:
                for raw_line in response:
                    if stop_event is not None and stop_event.is_set():
                        return
                    line = raw_line.decode("utf-8").strip()
                    if line.startswith("data: "):
                        try:
                            yield json.loads(line[6:])
                        except json.JSONDecodeError as exc:
                            raise NHRProtocolError(
                                "Measurement SSE contained invalid JSON"
                            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise NHRTransportError(
                f"Measurement SSE transport failed; instrument state is unknown: {exc}"
            ) from exc

    def events(
        self,
        instrument_id: str,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[RuntimeEventEnvelope]:
        """Yield typed v1 runtime SSE event envelopes."""
        if stop_event is not None and stop_event.is_set():
            return
        request = Request(
            self.base_url + f"/instruments/{instrument_id}/events",
            headers={"Accept": "text/event-stream"},
        )
        try:
            with urlopen(request, timeout=None) as response:
                event_name: str | None = None
                for raw_line in response:
                    if stop_event is not None and stop_event.is_set():
                        return
                    line = raw_line.decode("utf-8").strip()
                    if line.startswith("event: "):
                        event_name = line[7:]
                    elif line.startswith("data: "):
                        try:
                            payload = json.loads(line[6:])
                        except json.JSONDecodeError as exc:
                            raise NHRProtocolError("Runtime SSE contained invalid JSON") from exc
                        if event_name is not None and payload.get("event") != event_name:
                            raise NHRProtocolError(
                                "Runtime SSE event name does not match payload"
                            )
                        yield payload
                        event_name = None
        except (URLError, TimeoutError, OSError) as exc:
            raise NHRTransportError(
                f"Runtime SSE transport failed; instrument state is unknown: {exc}"
            ) from exc

    def observe_events(
        self,
        instrument_id: str,
        *,
        queue_size: int = 100,
        reconnect: bool = False,
        reconnect_delay_s: float = 1.0,
    ) -> ManagedEventObserver:
        """Create a context-managed background observer for runtime SSE events."""
        return ManagedEventObserver(
            self,
            instrument_id,
            queue_size=queue_size,
            reconnect=reconnect,
            reconnect_delay_s=reconnect_delay_s,
        )


@dataclass(frozen=True, slots=True)
class _PendingExternalSnapshot:
    sequence: int
    timestamp_utc: str
    health: str
    signals: dict[str, float | bool]


class ExternalSnapshotPublisher:
    """Single-owner synchronous publisher with explicit ambiguous-retry state."""

    def __init__(
        self,
        client: NHRServiceClient,
        instrument_id: str,
        source_id: str,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        self.client = client
        self.instrument_id = instrument_id
        self.source_id = source_id
        self.timeout_s = timeout_s
        self._next_sequence: int | None = None
        self._pending: _PendingExternalSnapshot | None = None

    @property
    def next_sequence(self) -> int | None:
        return self._next_sequence

    @property
    def pending_sequence(self) -> int | None:
        return None if self._pending is None else self._pending.sequence

    def synchronize_sequence(self) -> int:
        """Resume above the source's last service-accepted sequence."""
        if self._pending is not None:
            raise NHRPendingSnapshotError(
                "Retry the pending snapshot before synchronizing sequence state"
            )
        snapshot = self.client.interlocks(self.instrument_id)
        external_sources = snapshot.get("external_sources")
        if not isinstance(external_sources, Mapping):
            raise NHRProtocolError("Interlock response has no external_sources object")
        sources = external_sources.get("sources")
        if not isinstance(sources, list):
            raise NHRProtocolError("Interlock response has no external source list")
        next_sequence = 0
        for source in sources:
            if (
                not isinstance(source, Mapping)
                or source.get("source_id") != self.source_id
            ):
                continue
            sequence = source.get("sequence")
            if sequence is None:
                break
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or sequence < 0
            ):
                raise NHRProtocolError("External source sequence is invalid")
            next_sequence = sequence + 1
            break
        self._next_sequence = next_sequence
        return next_sequence

    def publish(
        self,
        *,
        timestamp_utc: str,
        health: str,
        signals: Mapping[str, float | bool],
    ) -> ExternalSnapshotReceipt:
        """Publish new data, refusing to replace an ambiguous pending snapshot."""
        if self._pending is not None:
            raise NHRPendingSnapshotError(
                "Retry the pending snapshot before publishing newer data"
            )
        if self._next_sequence is None:
            self.synchronize_sequence()
        assert self._next_sequence is not None
        self._pending = _PendingExternalSnapshot(
            sequence=self._next_sequence,
            timestamp_utc=timestamp_utc,
            health=health,
            signals=dict(signals),
        )
        return self.retry_pending()

    def retry_pending(self) -> ExternalSnapshotReceipt:
        """Retry the exact pending payload after an ambiguous transport result."""
        pending = self._pending
        if pending is None:
            raise NHRPendingSnapshotError("There is no pending snapshot to retry")
        try:
            receipt = self.client.submit_external_snapshot(
                self.instrument_id,
                self.source_id,
                sequence=pending.sequence,
                timestamp_utc=pending.timestamp_utc,
                health=pending.health,
                signals=pending.signals,
                timeout_s=self.timeout_s,
            )
        except NHRAPIError:
            self._pending = None
            self._next_sequence = None
            raise
        if (
            receipt.get("source_id") != self.source_id
            or receipt.get("sequence") != pending.sequence
        ):
            raise NHRProtocolError(
                "External snapshot receipt does not match the pending submission"
            )
        self._pending = None
        self._next_sequence = pending.sequence + 1
        return receipt


class ManagedEventObserver:
    """Own the worker thread and cancellation state for one SSE subscription.

    Read events with :meth:`get`. When ``needs_runtime_refresh`` becomes true,
    call ``client.runtime(instrument_id)`` because one or more events were lost.
    """

    def __init__(
        self,
        client: NHRServiceClient,
        instrument_id: str,
        *,
        queue_size: int = 100,
        reconnect: bool = False,
        reconnect_delay_s: float = 1.0,
    ) -> None:
        if queue_size <= 0 or reconnect_delay_s <= 0:
            raise ValueError("queue_size and reconnect_delay_s must be greater than zero")
        self.client = client
        self.instrument_id = instrument_id
        self.reconnect = reconnect
        self.reconnect_delay_s = reconnect_delay_s
        self._queue: queue.Queue[RuntimeEventEnvelope] = queue.Queue(
            maxsize=queue_size
        )
        self._stop = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self.needs_runtime_refresh = False
        self.last_sequence: int | None = None

    def __enter__(self) -> ManagedEventObserver:
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def start(self) -> ManagedEventObserver:
        """Start the observer once and return ``self``."""
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run,
                name=f"nhr-events-{self.instrument_id}",
                daemon=True,
            )
            self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for event in self.client.events(
                    self.instrument_id, stop_event=self._stop
                ):
                    sequence = event.get("sequence")
                    if isinstance(sequence, int):
                        if (
                            self.last_sequence is not None
                            and sequence != self.last_sequence + 1
                        ):
                            self.needs_runtime_refresh = True
                        self.last_sequence = sequence
                    if event.get("dropped_before", 0):
                        self.needs_runtime_refresh = True
                    try:
                        self._queue.put_nowait(event)
                    except queue.Full:
                        self.needs_runtime_refresh = True
                        try:
                            self._queue.get_nowait()
                        except queue.Empty:
                            pass
                        self._queue.put_nowait(event)
            except BaseException as exc:
                if self._stop.is_set():
                    return
                if not self.reconnect:
                    self._error = exc
                    return
                self.needs_runtime_refresh = True
            if not self.reconnect:
                return
            self.needs_runtime_refresh = True
            if self._stop.wait(self.reconnect_delay_s):
                return

    def get(self, timeout_s: float | None = None) -> RuntimeEventEnvelope:
        """Return the next event, raising the worker error after the queue drains."""
        if self._thread is None:
            self.start()
        try:
            return self._queue.get(timeout=timeout_s)
        except queue.Empty:
            if self._error is not None:
                raise self._error
            raise

    def refresh_runtime(self) -> RuntimeSnapshot:
        """Fetch a full snapshot and clear the local dropped-event indicator."""
        snapshot = self.client.runtime(self.instrument_id)
        self.needs_runtime_refresh = False
        return snapshot

    def close(self, timeout_s: float = 2.0) -> None:
        """Request cancellation and wait briefly for the background worker."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
