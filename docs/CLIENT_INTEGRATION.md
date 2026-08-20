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
