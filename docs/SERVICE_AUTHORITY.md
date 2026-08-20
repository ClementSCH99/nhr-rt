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
| Approved-workflow control | Reserved for the approved workflow API introduced after Milestone 1 |
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

## Shutdown and restart

For every configured instrument, service shutdown performs the following
bounded sequence:

1. request stop for an active legacy routine and wait for its thread;
2. disable the output and clear the arm lease;
3. disable the watchdog;
4. read back output state and watchdog state;
5. stop acquisition;
6. close the service-owned instrument connection.

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

## Responsibility split

| Component | Responsibility |
|---|---|
| `nhr-rt` 32-bit service | IVI connection, NHR facade, acquisition, safety enforcement and final safe state |
| 64-bit NHR clients | Request typed service operations and observe state; never own IVI or shared acquisition |
| CAN-PY | Capture/decode CAN and publish timestamped external values; future combined-session orchestration |
| Monitor | Read-only presentation; no connection, workflow or primitive-control authority |
