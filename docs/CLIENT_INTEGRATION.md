# 64-bit client integration

Install the service with 32-bit Python and the base client package in the
64-bit application. The base package has no IVI dependency.

```powershell
# 32-bit service environment
.\.venv32\Scripts\python.exe -m pip install -e ".[ivi]" --no-build-isolation
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

The supported compatibility boundary is `NHRServiceClient`, the documented
HTTP/SSE endpoints and their JSON fields. Internal module paths are not a
cross-project API. During v0.2.x the established top-level workflow aliases are
retained while generic names become canonical.

Until external interlocks are validated, remote write methods must remain
operator-controlled. A transport failure is not proof that hardware is safe;
query status/acquisition again or use independent local verification.

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

The acknowledgement is required only for physical starts. Reusing the same
`request_id` with the same request returns the original run instead of starting
a duplicate. `stop_workflow()` is also idempotent.

A client timeout or process exit does not stop a service-owned run. Reconnect
and query its `run_id`. During the service-owned final verification reconnect,
an SSE reader receives EOF and should follow its normal bounded reconnect
policy.
