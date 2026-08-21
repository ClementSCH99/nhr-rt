# Service authority contract

This contract applies to the versioned `/api/v1` service and the retained
v0.2.0 read aliases.

## Ownership

- The 32-bit `nhr-rt` service is the sole owner of IVI-COM, the physical NHR
  connection and continuous acquisition.
- `NHR9300` remains the only path from service code to a backend. HTTP handlers
  and clients never call a backend directly.
- A successful `connect` request initializes the service-owned runtime. It does
  not grant ownership to the caller.
- `disconnect` is a compatibility detach. It never stops acquisition, an active
  routine or the instrument connection. Only service shutdown or restart closes
  those resources.
- Each SSE handler owns only its bounded subscriber queue. Closing a stream
  unsubscribes that queue and cannot affect another observer.
- Configuration changes require a restart. There is no hot reload.

## Endpoint classes

| Class | Established endpoints |
|---|---|
| Read-only | `GET configuration`, inventory, status, measurement, acquisition, routine status and stream |
| Approved-workflow control | Workflow registry, preflight, start, run status and run-specific stop under `/api/v1` |
| Primitive compatibility control | `POST connect`, disconnect, limits, arm, command, original routine and stop |

The versioned form inserts `/api/v1` before the established path. For example,
`/instruments/{id}/measurement` is also available as
`/api/v1/instruments/{id}/measurement`. Legacy read responses retain their
v0.2.0 fields.

`connect` and `disconnect` retain compatibility lifecycle semantics and are not
energizing commands. On a simulator, the other primitive endpoints remain
available for development. On an IVI instrument, `limits`, `arm`, `command`,
the original routine and `stop` return HTTP 403 unless the local instrument
configuration contains:

```json
"primitive_compatibility_control": true
```

The option defaults to `false`, is evaluated at startup and is intentionally
reported by `GET /api/v1/configuration`. It is a migration option, not an
authentication boundary or workflow approval.

## Approved workflow authority

Approved workflows are a separate control class. The client may provide only a
registered `workflow_id`, its expected bundle digest, a UUID request identifier
and the fixed per-run acknowledgement. It cannot provide a profile path,
workflow JSON, CSV bytes, stage, limit, setpoint or primitive hardware command.

The service loads exact local bundle bytes at startup. A bundle includes the
workflow JSON and every referenced dynamic CSV. The configured digest, embedded
profile approvals, target instrument, resource and serial must all agree. Disk
drift after startup is rejected until the service is restarted.

`remote_workflow_control` defaults to `false`. A physical start additionally
requires the per-run `SUPERVISED_WORKFLOW_READY` acknowledgement and an
explicitly named `controlled_stop_policy` containing `approved: true` and its
reviewed `timeout_s`. No physical fallback timeout is supplied by the software.

Workflow runs and their stop events belong to the service. Loss of the client,
HTTP response or SSE observer does not stop a run and does not transfer
authority. Primitive state-changing endpoints return a conflict while an
approved workflow owns the instrument.

## Shutdown and restart

For every configured instrument, service shutdown performs the following
bounded sequence:

1. request cooperative stop for an active approved workflow;
2. use the configured emergency fallback if it does not finish in the allowed
   bound;
3. request stop for an active legacy routine and wait for its thread;
4. disable the output and clear the arm lease;
5. disable the watchdog;
6. read back output state and watchdog state;
7. stop acquisition;
8. close the service-owned instrument connection.

An active routine stop uses the existing v0.2.0 fail-closed emergency-stop
behavior. The future approved-workflow API will distinguish controlled stop
from emergency fallback. Shutdown failures are logged and surfaced; closing
the listening socket still proceeds.

The current software shutdown bound is three seconds. It supports deterministic
simulator and service cleanup only. It is not an approved physical fallback
delay and must not be used as hardware acceptance evidence.

Restart means complete shutdown followed by a new process and a fresh reading
of configuration. No client disconnect, transport failure or normal SSE EOF is
proof of physical safe state.

An approved workflow performs one service-owned verification reconnect after
its first cleanup readback. Acquisition pauses, SSE observers receive EOF, the
service reconnects without yielding ownership, verifies output/watchdog and
restarts acquisition. This is the sole workflow-specific exception to the rule
that client detach never closes shared resources.

## Responsibility split

| Component | Responsibility |
|---|---|
| `nhr-rt` 32-bit service | IVI connection, NHR facade, acquisition, safety enforcement and final safe state |
| 64-bit NHR clients | Request typed service operations and observe state; never own IVI or shared acquisition |
| CAN-PY | Capture/decode CAN and publish timestamped external values; future combined-session orchestration |
| Monitor | Read-only presentation; no connection, workflow or primitive-control authority |
