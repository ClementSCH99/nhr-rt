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

`ExternalSnapshotPublisher` implements only that sequence/retry state. It is
synchronous, has no worker thread and never retries automatically:

```python
from nhr9300 import ExternalSnapshotPublisher, NHRTransportError

publisher = ExternalSnapshotPublisher(
    client, instrument_id, "bms-main", timeout_s=0.5
)
try:
    publisher.publish(
        timestamp_utc=decoded_sample.timestamp_utc,
        health="ok",
        signals=decoded_sample.signals,
    )
except NHRTransportError:
    # Retry from the forwarding worker; do not replace it with newer data.
    publisher.retry_pending()
```

The first publication resumes above the sequence reported by `interlocks()`.
After an ambiguous transport or protocol result, `pending_sequence` remains set
until `retry_pending()` succeeds. A definitive API rejection clears the pending
payload and forces sequence resynchronization on the next publication. Use one
publisher instance and one forwarding worker per `source_id`.

## Reference producer

The dependency-free reference producer exercises this boundary without CAN,
DBC or hardware access:

```powershell
python .\examples\external_snapshot_producer.py `
  .\examples\external_snapshots.example.jsonl `
  --service-url http://127.0.0.1:9300 `
  --instrument-id sim-1 `
  --source-id bms-reference
```

Each JSON Lines record contains `health`, `signals`, and optionally the original
`timestamp_utc`. When omitted, the example inserts the current UTC time only to
generate simulator data. CAN-PY must always forward its decoded source
timestamp. The example performs one explicit retry after a transport failure;
production retry scheduling remains owned by the CAN-PY forwarding worker.

## CAN-PY integration contract

CAN-PY owns CAN acquisition, DBC decoding, external-source health and combined
session orchestration. It must not import an NHR backend or call primitive NHR
controls. The 32-bit service remains the sole owner of IVI-COM, acquisition,
approved workflows, safety actions and final safe state.

Use one forwarding worker with a bounded queue and one
`ExternalSnapshotPublisher` per `source_id`. The CAN receive/decode path must
never wait for HTTP. A published snapshot must contain every signal required
from that source by the active workflow. Publish only finite values in the
documented SI units and keep the signal names identical to the approved
workflow rules.

Set `timestamp_utc` from the decoded CAN data, never from HTTP forwarding time.
If a snapshot combines safety signals with different source timestamps, use
the oldest contributing timestamp so freshness remains conservative. Set
`health="ok"` only while the required CAN messages are current, decoding is
valid and the complete required signal set is available. Otherwise publish a
non-`ok` health when possible; silence also becomes unsafe through staleness.
No publication cadence is inherently safe: it must remain comfortably below
the smallest approved `max_age_s` used by the workflow.

### Lifecycle order

1. Create the client and verify `configuration()` compatibility.
2. Start CAN acquisition/decoding, then the bounded forwarding worker.
3. Publish a complete snapshot and confirm `interlocks()` before preflight.
4. Preflight/start only a registered workflow and retain its `run_id`.
5. Observe with `runtime()`/`events()` while publication remains active.
6. On an intentional stop, call `stop_and_wait_workflow()` and keep publishing
   until the run is terminal and final state is verified.
7. Stop SSE and forwarding workers with bounded joins, then close CAN capture.

Stopping CAN-PY, losing HTTP or closing an observer does not stop a service-
owned workflow and is not proof of physical safe state.

### Failure behavior

| Condition | Required CAN-PY behavior | NHR-RT consequence |
|---|---|---|
| HTTP result unknown | Retain and retry the exact pending snapshot | Idempotent retry; no newer sequence first |
| Definitive API rejection | Record `NHRAPIError.code`; do not retry blindly | Source remains fail-closed until valid data |
| CAN unhealthy or decode incomplete | Publish non-`ok` health when transport permits | Start blocked or runtime controlled stop |
| Publisher/service unavailable | Keep CAN capture independent; report NHR state unknown | Required data eventually becomes stale |
| SSE gap or `dropped_before` | Refresh `runtime()` | Safety/acquisition remain service-owned |
| CAN-PY shutdown with active run | Explicitly stop and wait before ending publication | Client exit alone does not stop the run |

### Combined evidence

CAN-PY owns its CAN evidence and the future combined-session manifest. Record
the NHR API/contract versions, `instrument_id`, `source_id`, DBC/mapping
identity, workflow ID/digest, request/run IDs and the terminal NHR report path.
Correlate files with UTC timestamps. SSE is live observability and must not be
used as a replacement for the NHR terminal report and referenced CSV evidence.

Dynamic SoP limiting remains outside this contract. Until Milestone 6 is
implemented and separately reviewed, CAN-PY must not interpret an external SoP
signal as an applied NHR power limit.

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

`observe_events(reconnect=True)` uses the interruptible default reconnect
backoff `0.5, 1, 2, 5` seconds and remains capped at 5 seconds. Passing
`reconnect_delay_s` explicitly preserves a fixed application-selected delay.

The runtime schema is documented in
[RUNTIME_OBSERVABILITY.md](RUNTIME_OBSERVABILITY.md). Snapshot publication,
typed rules, latching and stop behavior are documented in
[EXTERNAL_INTERLOCKS.md](EXTERNAL_INTERLOCKS.md).
