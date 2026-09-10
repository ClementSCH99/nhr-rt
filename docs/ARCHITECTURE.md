# Architecture

NHR Remote Testing separates hardware ownership from clients and user
interfaces. The 32-bit service is the only process allowed to load IVI-COM and
own a physical NHR connection. Python 64-bit clients communicate through the
localhost HTTP/SSE contract.

```text
64-bit client / future GUI / CAN-BMS adapter
                    |
              HTTP + SSE JSON
                    |
       32-bit nhr9300 service and workflow engine
                    |
         NHR9300 safety facade + IVI backend
                    |
                NHR hardware
```

## Module responsibilities

| Module | Responsibility |
|---|---|
| `types` | Stable domain values exchanged between layers |
| `backends` | Hardware protocol plus IVI and simulator implementations |
| `instrument` | Serialized backend access, safety limits, arm lease, interlocks and safe state |
| `interlocks` | Fail-closed provider contract and provider composition |
| `external_interlocks` | Snapshot validation, freshness, typed rule evaluation and runtime latching |
| `acquisition` | Timed measurement collection and in-memory publication |
| `sinks` | Persistence destinations that never access the instrument |
| `routines` | Conditions, steps, routine factories and single-routine execution |
| `profiles` | JSON/CSV loading and validation into typed workflow configuration |
| `sequences` | Ordered routines, shared acquisition, stage CSVs and directional totals |
| `execution` | Reusable preflight, execution, evidence and cleanup lifecycle |
| `workflow_registry` | Immutable startup bundles, approval metadata and canonical digests |
| `workflow_runs` | Per-instrument reservation, asynchronous state, cooperative stop and recovery manifests |
| `service` | Local HTTP/SSE adapter and instrument ownership |
| `client` | Dependency-free 64-bit client for the service contract |
| `cli` | Argument parsing and operator acknowledgement only |

The dependency direction points toward `types`, never toward the CLI or HTTP
layer. `service` owns `workflow_runs`, which injects the already-owned
instrument and collector into `execution`. Execution does not create a backend
on the service path and does not import a client or UI.

## Extension boundaries

- External BMS/CAN processes publish decoded snapshots through the versioned
  service API. The service-owned `ExternalInterlockManager` adapts approved
  workflow rules to `InterlockProvider`; missing, unsafe, rejected and stale
  data fail closed inside the instrument safety path.
- Advanced logging implements `MeasurementSink`. Sinks receive normalized rows
  and cannot block or call hardware APIs.
- A future GUI or 64-bit command client uses the existing service contract. It
  must not instantiate `IVIBackend`.
- New routine types are built from the same arm, measurement, setpoint and
  cleanup primitives rather than bypassing `NHR9300`.
