# 64-bit client integration

For interactive discovery, `help(NHRServiceClient)` and
`help(NHRServiceClient.wait_workflow)` describe ownership and timeout behavior.
Prefer `detach_observer()` over the deprecated, easily misunderstood
`disconnect()` alias, and `stop_legacy_routine()` when maintaining primitive
routine clients.

Install the service with 32-bit Python and the base client package in the
64-bit application. The base package has no IVI dependency.

```powershell
# 32-bit service environment
.\.venv32\Scripts\python.exe -m pip install -e ".[ivi]" --no-build-isolation
Copy-Item .\examples\service.hardware.example.json .\service.local.json
# Review the local resource, policies and any workflow registry before startup.
.\.venv32\Scripts\nhr9300-service.exe --config .\service.local.json
```

```python
from nhr9300 import NHRServiceClient

client = NHRServiceClient("http://127.0.0.1:9300")  # uses /api/v1
instrument_id = "nhr-79503"
client.connect(instrument_id)

for measurement in client.stream(instrument_id):
    timestamp_utc = measurement["timestamp_utc"]
    current_a = measurement["current_a"]
```

Run `stream()` in a dedicated thread and forward samples through a bounded
queue. Stop the stream before calling the compatibility `disconnect()` method.
That method detaches the observer; it does not stop the service-owned
acquisition or physical connection. Correlate CAN and NHR with
`timestamp_utc`; monotonic clocks are process-local and must not be compared
across processes.

New clients use `/api/v1` by default. `NHRServiceClient(..., api_version=None)`
is available for verifying a legacy read alias during migration; new
integrations should not select it.

At startup, verify compatibility metadata instead of inferring features from
the package version:

```python
configuration = client.configuration()
assert "v1" in configuration["api_versions"]
assert configuration["contracts"]["external_snapshot"] == "1.0"
assert "external_snapshot_publication" in configuration["capabilities"]
```

API failures expose `NHRAPIError.status`, `.error_type` and the stable `.code`.
Branch on `.code`, not on the human-readable exception message. Existing v1
clients that use only `error` and `type` remain compatible.

The v1 codes are `policy_rejected`, `interlock_unsafe`, `state_conflict`,
`invalid_request`, `request_failed` and `internal_error`.

The supported compatibility boundary is `NHRServiceClient`, the documented
HTTP/SSE endpoints and their JSON fields. Internal module paths are not a
cross-project API. During v0.2.x the established top-level workflow aliases are
retained while generic names become canonical.

The external-snapshot PUT updates decoded data only and has no NHR hardware
authority. Workflow starts and primitive writes retain their existing gates. A
transport failure is not proof that hardware is safe; query runtime/interlocks
again or use independent local verification.

Publish external snapshots outside the CAN receive/decode thread and set a
bounded `timeout_s`. If a response is lost, retry the exact same payload and
sequence before publishing newer data. See
[EXTERNAL_INTERLOCKS.md](EXTERNAL_INTERLOCKS.md) for sequence restart rules.

See [Service authority contract](SERVICE_AUTHORITY.md) for endpoint
classification, primitive compatibility policy and shutdown behavior.

## Approved workflows

The 64-bit client selects only a locally registered workflow. Obtain the digest
from the service, preflight it, then start it with a new request UUID:

```python
import time
import uuid

workflows = client.workflows(instrument_id)
workflow = next(item for item in workflows if item["workflow_id"] == "approved-id")
digest = workflow["bundle_digest"]

preflight = client.preflight_workflow(instrument_id, "approved-id", digest)
if not preflight["passed"]:
    raise RuntimeError(preflight["error"])

run = client.start_workflow(
    instrument_id,
    request_id=str(uuid.uuid4()),
    workflow_id="approved-id",
    bundle_digest=digest,
    operator_acknowledgement="SUPERVISED_WORKFLOW_READY",
)
while run["state"] not in {"passed", "stopped", "failed", "interrupted"}:
    time.sleep(0.25)
    run = client.workflow_run(instrument_id, run["run_id"])
```

Physical preflight includes IVI connection, limit readback, cleanup and an
independent verification reconnect. The client therefore uses a 30-second
preflight timeout while ordinary requests retain their 10-second timeout. A
caller may pass `timeout_s=` to `preflight_workflow()` when a reviewed bench
requires a different bound; changing this client wait does not change the
service-owned controlled-stop policy.

The acknowledgement is required only for physical starts. Reusing the same
`request_id` with the same request returns the original run instead of starting
a duplicate. `stop_workflow()` is also idempotent.

A client timeout or process exit does not stop a service-owned run. Reconnect
and query its `run_id`. During the service-owned final verification reconnect,
an SSE reader receives EOF and should follow its normal bounded reconnect
policy.

## Consolidated runtime observation

Milestone 3 clients should use `client.runtime(instrument_id)` for a coherent
read-only view and `client.events(instrument_id)` for live display updates. The
event stream is bounded and may report `dropped_before`; refresh `runtime()`
after any gap. Do not calculate a workflow percentage when
`progress_available` is false or `progress.percent` is `null`.

The runtime schema is documented in
[RUNTIME_OBSERVABILITY.md](RUNTIME_OBSERVABILITY.md). Snapshot publication,
typed rules, latching and stop behavior are documented in
[EXTERNAL_INTERLOCKS.md](EXTERNAL_INTERLOCKS.md).
