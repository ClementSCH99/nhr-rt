# How to operate NHR Remote Testing

## Purpose and safety boundary

This guide explains how to use `nhr9300-run`, `nhr9300-service`,
`NHRServiceClient`, the workflow registry, runtime/SSE observability and the
generated evidence. It includes simulation, diagnosis and supervised hardware
preparation examples.

Simulation and software tests are not physical validation. No example profile,
timeout, limit or acknowledgement in this repository is automatically approved
for hardware. Physical execution requires all of the following:

- an engineering-reviewed exact workflow bundle;
- the correct resource and serial number;
- approved safety limits and controlled-stop policy;
- a successful current-session preflight;
- an operator present at the bench;
- an accessible independent emergency stop;
- explicit authorization for that physical run.

Never interpret a client exception, closed console, HTTP timeout or SSE EOF as
proof that physical output is off. Confirm terminal run evidence and the final
safe-state readback.

## 1. Mental model

```text
64-bit client / monitor
        |
        | HTTP requests + SSE observations
        v
127.0.0.1:9300
32-bit nhr9300-service
        |
        | sole ownership of connection, acquisition and control
        v
Simulator or IVI-COM NHR9300
```

The persistent service owns instrument resources. Clients are replaceable
observers/requesters. Several consequences follow:

- closing a client does not stop a workflow;
- `connect()` asks the service to initialize its instrument runtime;
- compatibility `disconnect()` detaches the caller but does not close the
  service-owned connection or stop acquisition;
- an SSE subscriber owns only its event queue;
- service shutdown, not client shutdown, releases the service-owned resources.

## 2. Choose the operating mode

| Need | Use | Why |
|---|---|---|
| Validate one profile locally | `nhr9300-run --simulate` | Shortest autonomous path; process ends with the workflow |
| Run one supervised physical profile locally | `nhr9300-run --hardware` | Direct single-run lifecycle, still subject to all hardware gates |
| Serve several 64-bit clients | `nhr9300-service` | Persistent 32-bit owner with HTTP API |
| Remotely start only reviewed workflows | Service workflow registry | Client selects an immutable local workflow by ID and digest |
| Observe consolidated live state | `NHRServiceClient.runtime()` or monitor | Read-only status, measurements, limits, alerts and workflow state |
| Consume live updates | `client.events()` | SSE stream; suitable for UI, not durable evidence |
| Prove what happened | Terminal `report.json` and referenced CSVs | Durable run evidence |

Use the standalone runner while developing profile logic. Use the service when
the persistent ownership model, remote clients, live monitoring or registered
workflow authority is part of the scenario.

## 3. Requirements and environments

### Common requirements

- Windows for the physical IVI-COM driver;
- Python 3.12 or newer;
- this package installed in each environment that runs a CLI or imports the
  client;
- localhost access to the configured service port;
- a writable evidence directory.

### Recommended split

| Environment | Bitness | Typical contents | Purpose |
|---|---:|---|---|
| `.venv32` | 32-bit | base package, `ivi` extra, COM driver | service, physical backend, standalone runner |
| `.venv64` | 64-bit | base package and client application | client scripts and monitor |

The public client has no IVI dependency. It can run in 64-bit Python while the
32-bit service owns the physical driver.

Install the development/simulation environment:

```powershell
python -m pip install -e ".[test]"
```

Install the physical service dependencies in 32-bit Python:

```powershell
.\.venv32\Scripts\python.exe -m pip install -e ".[ivi]" --no-build-isolation
```

Confirm bitness and interpreter paths:

```powershell
.\.venv32\Scripts\python.exe -c "import struct,sys; print(struct.calcsize('P')*8, sys.executable)"
.\.venv64\Scripts\python.exe -c "import struct,sys; print(struct.calcsize('P')*8, sys.executable)"
```

Expected result: 32 bits for the service environment and 64 bits for the client
environment.

### Evidence directory recommendation

For repeatable qualification runs, prefer a local, non-synchronized path with
simple characters. OneDrive can temporarily lock files during atomic state
replacement. For example:

```json
{
  "output_dir": "C:/nhr-runs/session-2026-09-08"
}
```

Verify that the operator account can create, replace and remove files there
before the session. Do not silently redirect evidence after a run starts.

## 4. Discover the command line

Every CLI exposes its current options through `-h`:

```powershell
.\.venv32\Scripts\nhr9300-run.exe -h
.\.venv32\Scripts\nhr9300-service.exe -h
.\.venv64\Scripts\nhr9300-monitor.exe -h
```

Two commands inspect without connecting an instrument:

```powershell
nhr9300-doctor --config .\examples\service.simulator.json
nhr9300-bundle inspect .\path\workflow.json
nhr9300-bundle digest .\path\workflow.json
```

`nhr9300-doctor` does write/replace/delete a small diagnostic file below the
configured `output_dir`; it performs no backend connection or control action.

Use the installed executable from the intended virtual environment. This avoids
accidentally running a global or wrong-bitness installation.

## 5. First autonomous simulation

Start with a reviewed copy of an example, not a physical profile. The following
shape describes a 5-second rest:

```json
{
  "test_description": "Learning rest simulation",
  "bench_description": "Simulator only",
  "stop_procedure": "Simulator cleanup only",
  "expected_resource": "sim-learning",
  "expected_serial_number": "SIM-9300",
  "simulation_initial_voltage_v": 90.0,
  "watchdog_enabled": true,
  "safety_limits": {
    "charge_current": 5.0,
    "charge_voltage_max": 100.0,
    "charge_power": 500.0,
    "discharge_current": 5.0,
    "discharge_voltage_min": 80.0,
    "discharge_power": 500.0,
    "approved": true,
    "profile_name": "APPROVED FOR SIMULATION ONLY"
  },
  "workflow_limits": {
    "max_current_a": 3.0,
    "max_power_w": 250.0,
    "max_stage_duration_s": 10.0,
    "max_sequence_duration_s": 15.0,
    "approved": true,
    "profile_name": "APPROVED FOR SIMULATION ONLY"
  },
  "stages": [
    {"name": "rest", "type": "rest", "duration_s": 5.0}
  ]
}
```

Run it:

```powershell
.\.venv32\Scripts\nhr9300-run.exe --simulate `
  --profile .\runs\learning\rest.json
```

The command prints the report path and `PASS` or `FAIL`. Inspect the report even
after PASS: confirm the selected workflow, termination reason, elapsed time,
sample count and final safe state.

### Duration and termination are different

A stage without a termination condition passes when its duration elapses. A
stage with a termination condition must reach that condition before its
duration expires. The duration is then a maximum allowed time, not an alternate
successful termination.

Example constant-current stage:

```json
{
  "name": "constant-current",
  "type": "constant_current",
  "duration_s": 30.0,
  "mode": "charge",
  "current_a": 2.0,
  "voltage_v": 95.0,
  "power_w": 250.0,
  "voltage_limit_enabled": true,
  "current_limit_enabled": true,
  "power_limit_enabled": false,
  "termination": {
    "field": "voltage",
    "operator": ">=",
    "value": 94.0,
    "relative": false
  }
}
```

This passes only if measured voltage reaches at least 94 V within 30 seconds.
Otherwise the stage ends with a condition timeout. Removing `termination`
changes the contract: completing 30 seconds becomes the success condition.

## 6. Configure a simulator service

A minimal persistent simulator service:

```json
{
  "output_dir": "runs/learning/service-output",
  "event_queue_size": 100,
  "instruments": [
    {
      "id": "sim-1",
      "backend": "simulator",
      "rate_hz": 5,
      "remote_workflow_control": false
    }
  ],
  "workflow_registry": []
}
```

An empty registry means clients cannot start approved workflows. The service can
still provide inventory, configuration, status, acquisition and runtime APIs.

Configuration fields used by the current service:

| Location | Field | Default / meaning |
|---|---|---|
| top level | `output_dir` | `runs`; service acquisition and workflow evidence root |
| top level | `event_queue_size` | `100`; bounded queue per SSE subscriber |
| instrument | `id` | Required unique client-facing instrument ID |
| instrument | `backend` | `ivi` by default; use `simulator` for software-only work |
| instrument | `simulator_initial_state_policy` | `reset_from_profile`; currently the only supported simulator policy |
| instrument | `logical_name` | IVI resource name used by the physical backend |
| instrument | `driver_dll` | Optional explicit IVI driver DLL path |
| instrument | `rate_hz` | `5.0`; requested acquisition rate |
| instrument | `operator_supervised` | False for IVI, true for simulator by default; static interlock input |
| instrument | `primitive_compatibility_control` | False; gates sensitive legacy primitives on IVI |
| instrument | `remote_workflow_control` | False; gates registered workflow preflight/start |
| instrument | `controlled_stop_policy` | Required approved timeout policy for physical remote workflows |
| registry | `workflow_id` | Required unique selection ID |
| registry | `instrument_id` | Instrument allowed to execute the workflow |
| registry | `profile_path` | Local profile path, relative to service config when not absolute |
| registry | `expected_resource` | Must match the workflow profile |
| registry | `expected_serial_number` | Must match the workflow profile |
| registry | `expected_bundle_digest` | Must match canonical local bundle bytes |
| registry | `approved` | Must be true for the entry to be available |

The listening address is supplied on the command line, not in the JSON:

```powershell
.\.venv32\Scripts\nhr9300-service.exe `
  --config .\runs\learning\service.simulator.json `
  --host 127.0.0.1 `
  --port 9301
```

The service rejects non-localhost hosts. A client for the alternate port uses
`NHRServiceClient("http://127.0.0.1:9301")`.

Start the service in its own console:

```powershell
.\.venv32\Scripts\nhr9300-service.exe `
  --config .\runs\learning\service.simulator.json
```

Expected startup output includes the resolved configuration, listening URL,
instrument ID, backend, sample rate and general acquisition CSV path.

Leave this console open. Use another console for the 64-bit client.

### Stop the service

Use `Ctrl+C` in the service console and allow bounded cleanup to finish. Closing
a normal console often terminates its attached foreground process, but this is
not a substitute for observing controlled service cleanup.

Verify that nothing listens on port 9300:

```powershell
Get-NetTCPConnection -LocalPort 9300 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

No returned object means no matching listener was found at that instant.

## 7. Find the process that owns the service port

```powershell
$listener = Get-NetTCPConnection -LocalPort 9300 -State Listen
$listener | Select-Object LocalAddress, LocalPort, OwningProcess
Get-Process -Id $listener.OwningProcess |
  Select-Object Id, ProcessName, Path
```

If `Get-Process` says the PID no longer exists and a second listener query finds
nothing, the process exited between the two checks. PIDs are observations, not
stable identities across restarts.

Starting a second service on the occupied port should fail with:

```text
OSError: [WinError 10048] Only one usage of each socket address ...
```

The already-running service should keep its PID and listener. Stop the failed
second process, then verify the first service from a client.

## 8. Basic 64-bit client use

```python
import sys
from nhr9300 import NHRServiceClient

print(f"64-bit Python: {sys.maxsize >= 2**32}")

client = NHRServiceClient("http://127.0.0.1:9300")
print(client.instruments())
print(client.configuration())
```

`instruments()` is the live inventory/status view. `configuration()` reports
the service configuration in effect, including backend type, feature flags,
requested rate and evidence paths. Configuration changes require service
restart.

### Connect and inspect acquisition

```python
import time
from nhr9300 import NHRServiceClient

client = NHRServiceClient()
client.connect("sim-1")

before = client.acquisition("sim-1")["sample_count"]
time.sleep(2.0)
after = client.acquisition("sim-1")["sample_count"]

print(f"Samples: {before} -> {after}")
print(client.status("sim-1"))
print(client.measurement("sim-1"))
```

At 5 Hz, approximately ten additional samples over two seconds are expected.
Scheduling and lifecycle boundaries can cause small differences.

### Why `disconnect()` does not make `connected` false

```python
client.disconnect("sim-1")
print(client.instruments())
```

This compatibility method detaches the observer; it does not close the shared
service-owned connection. Therefore `connected` can remain true and the sample
count can continue growing. Stop the service to close its resources.

### Isolate client errors

```python
from nhr9300 import NHRServiceClient
from nhr9300.errors import NHRError

client = NHRServiceClient()

try:
    client.connect("does-not-exist")
except NHRError as exc:
    print(type(exc).__name__)
    print(exc)

print(client.instruments())
```

The bad request should not terminate the service or another instrument's
acquisition.

### Client API quick reference

| Method | Class | Main result / behavior |
|---|---|---|
| `instruments()` | Read | Inventory plus initialized status when available |
| `configuration()` | Read | Effective service settings and evidence paths |
| `status(id)` | Read | Instrument status and setpoints |
| `measurement(id)` | Read | Latest measurement |
| `acquisition(id)` | Read | Collector state, sample count and CSV path |
| `runtime(id)` | Read | Consolidated diagnostic/monitor snapshot |
| `stream(id, stop_event=...)` | Read stream | Compatibility measurement SSE iterator |
| `events(id, stop_event=...)` | Read stream | Typed runtime SSE event iterator |
| `workflows(id)` | Read | Registered workflow summaries and availability |
| `workflow_run(id, run_id)` | Read | Current/persisted workflow run snapshot |
| `connect(id)` | Lifecycle | Initializes service-owned runtime; does not transfer ownership |
| `disconnect(id)` | Lifecycle | Compatibility observer detach; shared resources continue |
| `preflight_workflow(...)` | Workflow control | Synchronous approved workflow preflight |
| `start_workflow(...)` | Workflow control | Asynchronous registered workflow start |
| `stop_workflow(id, run_id)` | Workflow control | Idempotent cooperative-stop request |
| `configure_limits(id, limits)` | Primitive compatibility | Programs primitive limits when policy permits |
| `arm(id, duration_s)` | Primitive compatibility | Creates a bounded primitive arm lease |
| `command(id, name, **kwargs)` | Primitive compatibility | Sends a typed primitive command when permitted |
| `start_routine(id, definition)` | Primitive compatibility | Starts the legacy routine API |
| `routine(id)` | Primitive compatibility read | Reads legacy routine state |
| `stop(id)` | Primitive compatibility | Stops the legacy routine, not an approved workflow |

Normal requests use a 10-second client HTTP timeout. Workflow preflight uses a
30-second default and exposes `timeout_s`. SSE streams have no read timeout and
therefore require explicit cancellation/lifecycle management.

## 9. Runtime snapshot

```python
from pprint import pprint
from nhr9300 import NHRServiceClient

client = NHRServiceClient()
pprint(client.runtime("sim-1"))
```

Use runtime when a UI or diagnostic needs one coherent snapshot. Check:

- instrument connection, operating state and enabled/output state;
- measurement value, timestamp and freshness;
- acquisition status, count, rate and CSV path;
- active workflow/run and current stage;
- charge/discharge Ah and Wh;
- external-source availability;
- interlocks, limits and active alerts.

Numeric instrument state `1` means `STANDBY` in the current implementation.
Do not infer electrical isolation solely from a state number or a 0 W command.

## 10. Observe live SSE events

`events()` blocks while waiting for server-sent events. Run it in a worker
thread when the main program must continue:

```python
import threading
import time
from collections import Counter
from nhr9300 import NHRServiceClient

client = NHRServiceClient()
cancel = threading.Event()
received = []


def collect_events(instrument_id: str, stop_event: threading.Event) -> None:
    try:
        for event in client.events(
            instrument_id=instrument_id,
            stop_event=stop_event,
        ):
            received.append(event)
    except Exception as exc:
        # Transport loss is diagnostic information, not safe-state proof.
        received.append({"event": "client_error", "error": str(exc)})


worker = threading.Thread(
    target=collect_events,
    args=("sim-1", cancel),
    name="nhr-events-sim-1",
    daemon=True,
)
worker.start()

time.sleep(5.0)
cancel.set()
worker.join(timeout=5.0)

if worker.is_alive():
    raise RuntimeError("SSE worker did not stop within 5 seconds")

counts = Counter(item.get("event", "unknown") for item in received)
print(counts)
```

Important details:

- instantiate the cancellation event with `threading.Event()`;
- iterate over `client.events(...)`; it is not one event per call;
- call `set()` during cleanup;
- use a bounded `join()` and verify `is_alive()`;
- treat measurement/interlock/limit/workflow repetition as expected live state;
- if the stream reports dropped events, refresh `runtime()`;
- use reports and CSVs, not SSE history, as the durable record.

## 11. Register an approved simulation workflow

Clients cannot send profile files to the service. The service configuration
references a local immutable bundle:

```json
{
  "output_dir": "runs/learning/registered-output",
  "event_queue_size": 100,
  "instruments": [
    {
      "id": "sim-l3",
      "backend": "simulator",
      "rate_hz": 5,
      "remote_workflow_control": true
    }
  ],
  "workflow_registry": [
    {
      "workflow_id": "learning-rest",
      "instrument_id": "sim-l3",
      "profile_path": "learning-rest.json",
      "expected_resource": "sim-l3",
      "expected_serial_number": "SIM-9300",
      "expected_bundle_digest": "sha256:REPLACE_WITH_COMPUTED_DIGEST",
      "approved": true
    }
  ]
}
```

Path rules:

- a relative registry `profile_path` is resolved relative to the service
  configuration file;
- `output_dir` is optional and defaults to `runs`; a relative value follows the
  service process working directory;
- use absolute output paths for qualification sessions when ambiguity is not
  acceptable.

`remote_workflow_control` defaults to false. Setting it true makes this
registered control class available; it is not network authentication and does
not approve arbitrary commands.

### Compute and inspect the bundle digest

```python
from pathlib import Path
from nhr9300 import WorkflowBundle

bundle = WorkflowBundle.load(
    Path("runs/learning/learning-rest.json"),
    hardware=False,
)

print(bundle.digest)
print(bundle.files.keys())
print(bundle.source_paths)
```

The logical bundle normally contains `workflow.json` even if the source file is
named `learning-rest.json`. Referenced profile CSVs appear under their canonical
logical names. `source_paths` retains the mapping to disk.

The digest covers the canonical manifest of all bundle bytes. JSON whitespace,
CSV line endings or any value change can change it. Copy the computed value into
`expected_bundle_digest`, then restart the service.

The digest does not include the registry's `expected_resource` or
`expected_serial_number`; those are separate identity gates. It also does not
mean that engineering approved the content.

### Confirm registry availability

```python
from pprint import pprint
from nhr9300 import NHRServiceClient

client = NHRServiceClient()
for workflow in client.workflows("sim-l3"):
    pprint(workflow)
```

Before preflight, confirm:

- the expected `workflow_id` and `instrument_id`;
- `available` is true and `error` is null;
- the returned digest matches the locally reviewed bundle;
- the profile/test description and stage count are expected.

## 12. Understand all digest failures

### Wrong digest sent by the client

```python
from nhr9300 import NHRServiceClient
from nhr9300.errors import NHRError

client = NHRServiceClient()
workflow = client.workflows("sim-l3")[0]

try:
    client.preflight_workflow(
        instrument_id="sim-l3",
        workflow_id=workflow["workflow_id"],
        bundle_digest="sha256:" + "0" * 64,
    )
except NHRError as exc:
    print(exc)
```

Expected error: `Client bundle digest does not match the registry`.

### Bundle changed after service startup

If any bundle file changes after startup, preflight/start rejects it with
`Workflow bundle changed after service startup; restart required`.

Recovery sequence:

1. stop editing and review the new exact files;
2. compute the new bundle digest;
3. update `expected_bundle_digest` in service configuration;
4. stop the service cleanly;
5. restart it with that configuration;
6. compare client and registry digests;
7. repeat preflight.

Never bypass drift protection by reusing a previous digest.

## 13. Preflight a registered workflow

```python
from pathlib import Path
from nhr9300 import NHRServiceClient, WorkflowBundle
from nhr9300.errors import NHRError

instrument_id = "sim-l3"
workflow_id = "learning-rest"

bundle = WorkflowBundle.load(
    Path("runs/learning/learning-rest.json"),
    hardware=False,
)
client = NHRServiceClient()

registry_items = client.workflows(instrument_id)
workflow = next(
    item for item in registry_items if item["workflow_id"] == workflow_id
)

if workflow["bundle_digest"] != bundle.digest:
    raise NHRError("Local reviewed bundle does not match service registry")

preflight = client.preflight_workflow(
    instrument_id=instrument_id,
    workflow_id=workflow_id,
    bundle_digest=bundle.digest,
    timeout_s=30.0,
)

if not preflight["passed"]:
    raise NHRError(preflight.get("error") or "Preflight failed")

print(preflight["preflight_id"])
print(preflight["report_path"])
print(preflight["checks"])
```

Preflight is complete when the call returns. It proves the checks recorded in
that preflight report, including final safe-state verification. It does not
prove that a CC stage reaches voltage, that a dynamic CSV behaves correctly or
that the complete workflow will pass.

## 14. Start and wait for a workflow

The following reusable helper polls an asynchronous run without hiding failures:

```python
import time
import uuid
from nhr9300 import NHRServiceClient

TERMINAL_STATES = {"passed", "stopped", "failed", "interrupted"}


def wait_for_terminal(
    client: NHRServiceClient,
    instrument_id: str,
    run_id: str,
    timeout_s: float,
    poll_s: float = 0.5,
) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        snapshot = client.workflow_run(instrument_id, run_id)
        if snapshot["state"] in TERMINAL_STATES:
            return snapshot
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Client stopped waiting for run {run_id}; "
                "the service run may still be active"
            )
        time.sleep(poll_s)


client = NHRServiceClient()
workflow = client.workflows("sim-l3")[0]

run = client.start_workflow(
    instrument_id=workflow["instrument_id"],
    request_id=str(uuid.uuid4()),
    workflow_id=workflow["workflow_id"],
    bundle_digest=workflow["bundle_digest"],
    operator_acknowledgement="",
)

terminal = wait_for_terminal(
    client,
    instrument_id=workflow["instrument_id"],
    run_id=run["run_id"],
    timeout_s=120.0,
)

print(terminal["state"])
print(terminal["report_path"])
print(terminal["final_safe_state"])
```

Use a new UUID for a new requested run. Reusing the same request UUID and bundle
is an idempotent retry and should return the original run rather than create a
duplicate.

The simulator does not require an operator acknowledgement. Physical workflow
start requires `SUPERVISED_WORKFLOW_READY` plus independent per-run
authorization; the string is a software gate, not the authorization itself.

## 15. Request cooperative stop correctly

Stop while the run is active, then poll. Do not wait for natural completion and
call stop afterward if the goal is to test cooperative stop.

```python
import time
from nhr9300 import NHRServiceClient

client = NHRServiceClient()
instrument_id = "sim-l4"
run_id = "REPLACE_WITH_ACTIVE_RUN_ID"

before = client.workflow_run(instrument_id, run_id)
print("Before stop:", before["state"])

first = client.stop_workflow(instrument_id, run_id)
second = client.stop_workflow(instrument_id, run_id)
print("First request:", first["state"])
print("Repeated request:", second["state"])

run = client.wait_workflow(instrument_id, run_id, timeout_s=60.0)

print("Terminal state:", run["state"])
print("Final safe state:", run["final_safe_state"])
print("Report:", run["report_path"])
```

Both stop calls can return `stop_requested` because cleanup is asynchronous.
That is expected and demonstrates idempotence. Terminal `stopped` confirms that
the service completed the cooperative-stop lifecycle; then inspect the report's
safe-state evidence.

Do not substitute:

- `client.disconnect()`, which is observer detach;
- `client.stop()`, which targets the legacy primitive routine API;
- killing the client process, which does not own the workflow.

For normal application code, the combined helper is shorter:

```python
run = client.stop_and_wait_workflow(
    instrument_id, run_id, timeout_s=60.0, poll_interval_s=0.5
)
```

A `NHRWorkflowTimeout` never sends an implicit stop request.

## 16. Recover after a client closes or times out

Workflow execution belongs to the service. If the client disappears:

1. start a new client process;
2. query `runtime(instrument_id)`;
3. obtain the known `run_id` from the original response/log or active runtime;
4. query `workflow_run(instrument_id, run_id)`;
5. resume observation or request `stop_workflow()` if authorized;
6. wait for a terminal state and inspect the report.

A client-side polling timeout deliberately does not send a stop request. The
exception message in the example helper makes this explicit.

After a service restart, persisted non-terminal runs can become `interrupted`.
A successful recovery preflight is required before a new start when the service
reports that condition.

## 17. Understand the generated files

Typical service output can include:

```text
output_dir/
  sim-l3_<timestamp>.csv                 general service acquisition
  workflow-preflights/
    preflight-.../
      report.json                        preflight evidence
  workflow-runs/
    <run-id>/
      run-state.json                     durable lifecycle snapshot
      report.json                        terminal workflow report
      ...sequence....csv                 complete workflow samples
      ...stage....csv                    stage-specific samples
```

Exact filenames are reported by the software. Do not select evidence solely by
directory ordering.

Why multiple general CSVs may appear:

- preflight can connect and clean up;
- workflow startup/cleanup changes acquisition ownership;
- final safe-state verification performs a service-owned reconnect;
- general acquisition can restart after the workflow ends.

The general CSV may therefore continue growing after terminal `stopped` or
`passed`. Determine workflow activity from `workflow_run()`/`runtime()`, and use
the files referenced by the terminal report for workflow evidence.

### Reconcile a terminal report

For every run, verify at least:

- `workflow_id`, `bundle_digest`, instrument identity and run ID;
- accepted, started and ended timestamps;
- terminal state/outcome and termination reason;
- stage count and completed/failed stage;
- sequence and per-stage sample statistics;
- directional charge/discharge Ah and Wh;
- requested limits and their readback;
- `final_safe_state` or equivalent verified safe cleanup;
- file paths and hashes for referenced evidence.

Start with the report's `artifact_manifest_path`. Its `artifacts.json` labels
each known file as `report`, `workflow_profile`, `workflow_dynamic_profile`,
`workflow_sequence` or `workflow_stage` and includes identity, size and hash.

If a report says PASS with an impossible initial voltage, near-zero duration or
zero samples/energy for an active stage, treat it as suspect even though its
state is `passed`.

## 18. Run the read-only monitor

With the service running, start the monitor in the 64-bit environment:

```powershell
.\.venv64\Scripts\nhr9300-monitor.exe --instrument-id sim-1
```

Open `http://127.0.0.1:9400`. The monitor presents runtime state and has no NHR
control authority. Closing it does not stop the service or workflow.

Both service and monitor are designed for localhost. The service has no network
authentication boundary; do not expose port 9300 to another host.

## 19. Primitive compatibility API

The older primitive endpoints include limits, arm, command, routine start and
routine stop. They are useful for controlled migration and simulator
development, but they are not the preferred remote hardware workflow path.

For a physical IVI instrument they are denied unless the local service config
explicitly contains:

```json
{
  "primitive_compatibility_control": true
}
```

The default is false. The flag is evaluated at service startup and visible in
`configuration()`. It is neither authentication nor approval. On the simulator,
primitive operations remain available for development under the current
contract.

Prefer the immutable workflow registry for repeatable remote execution. It
prevents clients from supplying arbitrary profiles, setpoints or limits.

## 20. Physical workflow preparation — no energizing

This section stops at validation/preflight unless a separately authorized and
supervised run is underway.

### Service configuration requirements

A physical instrument entry needs the reviewed resource, remote workflow flag
and approved controlled-stop policy. Conceptual shape:

```json
{
  "id": "nhr-reviewed-id",
  "backend": "ivi",
  "logical_name": "REPLACE_WITH_DISCOVERED_RESOURCE",
  "rate_hz": 5,
  "operator_supervised": false,
  "primitive_compatibility_control": false,
  "remote_workflow_control": true,
  "controlled_stop_policy": {
    "timeout_s": "REPLACE_WITH_REVIEWED_VALUE",
    "approved": true,
    "profile_name": "REPLACE_WITH_APPROVED_POLICY_NAME"
  }
}
```

Do not copy simulator identities, example limits or placeholder timeouts into a
physical configuration. `operator_supervised` must reflect the current bench
condition: keep it false without an operator, and set it true only for a
supervised, explicitly authorized session. Configuration changes require a
service restart.

### Preflight-only standalone command

After confirming the exact reviewed local profile and bench identity:

```powershell
$env:NHR9300_RESOURCE = "REPLACE_WITH_DISCOVERED_RESOURCE"
$env:NHR9300_WORKFLOW_ACK = "SUPERVISED_WORKFLOW_READY"
.\.venv32\Scripts\nhr9300-run.exe --hardware --preflight-only `
  --profile .\path\approved-workflow.local.json
```

Keep `--preflight-only` until the separate physical-run gate is explicitly
approved. Passing software tests or simulator exercises does not remove this
gate.

### Before any authorized physical start

Confirm and record:

- correct instrument logical name, resource, serial and calibration status;
- no other service/process owns the resource;
- approved exact bundle digest and no file drift;
- approved safety limits and readback;
- approved controlled-stop timeout and stop procedure;
- output and watchdog initially off;
- fresh measurements and acceptable interlocks;
- operator name/acknowledgement and emergency-stop access;
- evidence output directory is local, writable and identified;
- CAN/external sources required by the workflow are configured and fresh;
- independent recovery path if the client, service or communication fails.

## 21. Diagnostic playbook

### Service unavailable or connection refused

1. Check whether the service console is still running.
2. Query the listener and owner PID.
3. Confirm the client URL matches the configured `listen_url`.
4. Confirm localhost/firewall policy was not changed.
5. Read the service console error before restarting.

```powershell
Get-NetTCPConnection -LocalPort 9300 -State Listen -ErrorAction SilentlyContinue
Get-Process | Where-Object { $_.ProcessName -match 'python|nhr9300' } |
  Select-Object Id, ProcessName, Path
```

Safety note: if a physical workflow was active, a lost HTTP connection does not
prove it stopped. Use the independent bench stop/recovery procedure.

### Port 9300 already in use

Find `OwningProcess`, inspect its path and determine whether it is the intended
service. Do not kill an unknown process by guessing. If it is the valid service,
use it or stop it cleanly from its console.

### Wrong Python bitness

```powershell
python -c "import struct,sys; print(struct.calcsize('P')*8, sys.executable)"
```

The physical service requires the 32-bit IVI-compatible interpreter. Client and
monitor normally use 64-bit Python.

### Unknown instrument

- compare spelling/case with `client.instruments()`;
- inspect `client.configuration()`;
- confirm the intended config file was printed at service startup;
- restart after editing configuration.

### Workflow missing or unavailable

```python
for item in client.workflows("sim-l3"):
    print(item["workflow_id"], item["available"], item["error"])
```

Check registry approval, instrument mapping, resolved profile path, embedded
profile approvals, identity and startup digest.

### Digest mismatch

Compare three values: locally computed `bundle.digest`, service
`workflows()[...]["bundle_digest"]`, and configured
`expected_bundle_digest`. If files changed after startup, restart only after
reviewing them and updating the expected digest.

### `connected` remains true after `disconnect()`

Expected compatibility behavior. The shared connection belongs to the service.
Use runtime and service shutdown semantics, not observer detach, to reason about
resource ownership.

### Sample count or CSV keeps growing after client exit

Expected while service acquisition remains active. Confirm active workflow
separately with `runtime()` and `workflow_run()`.

### Stop returns `stop_requested`

Expected intermediate state. Poll the run until a terminal state. Repeating the
same stop request is safe and idempotent.

### Too many CSV files

Identify each file through its report/path and lifecycle. Expect general service
acquisition, sequence, per-stage and rotated acquisition files. A growing
general CSV is not a running-workflow indicator.

### Simulator initial voltage

The service currently exposes one deliberate policy: `reset_from_profile`.
Before each registered simulated preflight/run it disconnects the simulated
runtime, resets voltage/counters from `simulation_initial_voltage_v`, then
reconnects. Confirm the policy in `configuration()` and the selected/actual
voltage in `report["simulator_initial_state"]`.

### Windows access denied for `run-state.json.tmp`

This can occur in a synchronized OneDrive directory. Preserve the failed report
and console error; do not relabel the run as PASS. Move future session output to
an approved local non-synchronized directory, verify write/replace access, then
repeat the complete preflight/run with a new run ID.

### SSE stops or reports dropped events

- inspect the worker exception and whether cancellation was requested;
- query `runtime()` for a fresh full snapshot;
- reconnect the observer if appropriate;
- do not reconstruct durable evidence from an incomplete SSE stream;
- do not infer workflow termination from SSE EOF.

### Measurement is stale or unavailable

- inspect connection/acquisition state and last error in runtime;
- compare timestamps and requested acquisition rate;
- verify that preflight or reconnect is not temporarily rotating acquisition;
- fail closed for any control decision that requires a fresh measurement.

### Workflow is `interrupted`

Inspect the persisted run state and service startup logs. Confirm physical safe
state independently. Complete the required recovery preflight before starting a
new workflow; do not simply delete evidence or reuse the old request ID.

### External sources say `not_configured`

They are not active protections. If a workflow requires CAN, SoP or another
external input, configure and validate that source explicitly. Do not interpret
an absent source as a satisfied limit.

## 22. Safe exception handling pattern

```python
from nhr9300 import NHRAPIError, NHRServiceClient, NHRTransportError

client = NHRServiceClient()

try:
    snapshot = client.runtime("sim-1")
except NHRAPIError as exc:
    print(f"Service rejected the request ({exc.status}): {exc}")
except NHRTransportError as exc:
    print(f"Transport failed: {exc}")
    print("Instrument/workflow state is unknown; verify independently")
else:
    print(snapshot)
```

The exact low-level transport exception can vary. Preserve its traceback in
diagnostic logs. Never place a blanket `except Exception: pass` around a start,
stop or safe-state verification.

## 23. Shutdown checklist

### Simulator session

1. Wait for terminal workflow state or request stop and poll terminal.
2. Record report path and final safe state.
3. Stop SSE workers with bounded joins.
4. Stop the service with `Ctrl+C` and let cleanup finish.
5. Verify port 9300 no longer listens.
6. Archive the intended report and its referenced evidence.

### Physical session

In addition to the approved bench procedure:

1. verify terminal run state and final safe-state readback;
2. independently confirm output and watchdog state at the bench;
3. preserve all errors and recovery actions;
4. stop the service through controlled shutdown;
5. confirm instrument/resource ownership was released;
6. archive the exact bundle, configuration, report and referenced CSVs;
7. treat any unresolved contradiction as a failed/incomplete session.

## 24. Quick operating checklists

### Profile-development loop

1. Copy/create simulation-only profile.
2. Review limits, duration and termination semantics.
3. Run `nhr9300-run --simulate`.
4. Inspect report, sequence CSV and stage CSVs.
5. Correct the profile and rerun with new evidence.
6. Only after review, compute/register the immutable bundle for service use.

### Registered service workflow

1. Compute local bundle digest.
2. Configure registry identity, path, digest and approval.
3. Enable remote workflow control for the intended instrument.
4. Start/restart service and inspect `configuration()`.
5. Confirm workflow availability and digest.
6. Run synchronous preflight and require `passed`.
7. Start with a unique request UUID.
8. Observe runtime/SSE and poll the run.
9. If stopping, request once or repeatedly, then poll terminal.
10. Reconcile report and CSV evidence.

### Diagnostic minimum dataset

When reporting a problem, capture:

- exact command and interpreter path/bitness;
- package/revision and service config path;
- service startup/log output;
- instrument and workflow IDs, bundle digest and run/preflight ID;
- listener PID/port state;
- `configuration()`, `instruments()` and relevant `runtime()` snapshot;
- complete exception type/message/traceback;
- terminal report and the names/sizes of referenced files;
- whether the path is local or synchronized;
- whether the scenario was simulator, preflight-only or physical hardware.

Never include secrets or unrelated proprietary data in a support bundle.

## 25. Current known limitations

- The simulator supports only `reset_from_profile`; intentional state
  preservation between workflows is not yet supported.
- Windows/OneDrive locking is retried for a bounded interval, but persistent
  locks still fail closed; prefer a verified local evidence path.
- `disconnect()` is a compatibility detach despite its name.
- Workflow stop requires polling after `stop_requested`.
- SSE is a live bounded stream and can contain gaps; it is not the audit record.
- General service-acquisition rotations are not all attached to a workflow
  manifest because they can span several workflow lifecycles.
- Primitive compatibility control is a migration flag, not access control.
- The localhost service has no network authentication boundary.

See [Operator learning findings and improvement backlog](OPERATOR_LEARNING_FINDINGS.md)
for proposed product corrections and acceptance criteria.

## 26. Reference documentation

- [Architecture](ARCHITECTURE.md)
- [Safety](SAFETY.md)
- [Service authority contract](SERVICE_AUTHORITY.md)
- [64-bit client integration](CLIENT_INTEGRATION.md)
- [Workflow profiles](WORKFLOWS.md)
- [Runtime observability](RUNTIME_OBSERVABILITY.md)
- [Monitor](MONITOR.md)
- [Validation record](VALIDATION.md)
