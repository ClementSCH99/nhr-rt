# Architecture and design

## Purpose

NHR-RT separates the process that owns a cycler from the applications that
request tests or display data. This boundary exists because the vendor IVI-COM
driver requires 32-bit Windows, COM calls need thread affinity, and a client or
UI must not accidentally inherit hardware authority.

The supported dependency path is:

```text
operator / CAN-PY / 64-bit application / monitor
                         |
                  public HTTP + SSE
                         |
       nhr9300-service (32-bit, localhost authority)
             |                 |               |
       workflow runs      acquisition      observability
             |                 |               |
             +---------- NHR9300 facade -------+
                               |
                    IVI backend or simulator
```

No service handler, client, monitor, workflow or adapter may bypass the
`NHR9300` facade to call a backend directly.

## Ownership and responsibilities

| Component | Owns | Does not own |
|---|---|---|
| 32-bit service | IVI-COM, physical connection, acquisition, registered workflows, safety actions, evidence and final safe state | CAN capture, DBC decoding or user approval |
| `NHR9300` facade | serialized backend calls, approved limits, arm lease, measurement freshness, interlocks, safe transitions and emergency stop | HTTP, profile parsing or UI state |
| Workflow engine | validated stages, run exclusivity, cooperative stop, totals, manifests and cleanup | arbitrary client commands |
| 64-bit client | typed requests and observations through `/api/v1` | IVI-COM, shared acquisition or final safe-state authority |
| Monitor | read-only presentation of `runtime()` | connect, preflight, start, stop or setpoints |
| CAN-PY / BMS adapter | CAN acquisition, DBC decoding, source timestamps and health, bounded snapshot forwarding, CAN evidence | NHR control, NHR CSV or physical safety authority |
| Operator / test owner | DUT limits, profile approval, stop policy, physical setup and per-run authorization | software-inferred approval |

Configuration is loaded once at service startup. A change to a JSON profile,
referenced CSV, registry entry or policy requires review and a full restart;
there is no hot reload.

## Module map

| Module(s) | Role and rationale |
|---|---|
| `types`, `errors` | Stable domain objects and typed failure categories shared by all layers. They keep transport messages and backend details from leaking into test logic. |
| `backends.base` | Narrow hardware protocol used by the facade. |
| `backends.ivi` | Vendor IVI-COM adapter. It initializes without reset or identity-query side effects and is confined behind the facade's COM worker. |
| `backends.simulator` | Deterministic software backend with profile-controlled initial voltage. It validates logic, not physical behavior. |
| `instrument` | Central safety facade and the only backend caller. It serializes access and converges failures toward a disabled state. |
| `interlocks`, `external_interlocks` | Composable local interlocks plus strict timestamped external rules. Missing, unhealthy, invalid, stale or out-of-order required data fails closed. |
| `arm_lease` | Service-owned, bounded authority for long active stages. Renewal depends on fresh acquisition, unchanged limits/setpoints, watchdog and safe interlocks. |
| `acquisition`, `sinks` | Timed measurement collection, SSE publication and CSV persistence. Sinks cannot access hardware. |
| `routines` | Small safety-aware execution primitives and CC/CP/rest factories. |
| `profiles`, `cc_profiles`, `sequences` | Parse JSON/CSV into typed, validated workflows; compose CCCV, dynamic profiles and ordered stages; compute directional totals. |
| `execution` | Shared preflight, execution, report, artifact manifest and final cleanup lifecycle. |
| `workflow_registry` | Immutable startup snapshot of reviewed workflow bytes and canonical SHA-256 bundle identity. |
| `workflow_runs` | Per-instrument exclusivity, asynchronous state, request idempotence, cooperative stop, interrupted-run recovery and durable manifests. |
| `service` | Local HTTP/SSE adapter and lifetime owner of instruments, collectors and run controllers. |
| `client` | Dependency-free public client, external snapshot publisher and managed SSE observer for standard 64-bit Python. |
| `observability`, `monitor` | Cached runtime snapshots, bounded event fan-out and localhost-only read-only UI. Observability is isolated from acquisition and safety timing. |
| `operator` | Guided console that prepares a temporary config, launches service/monitor processes and makes uncertain-start recovery explicit. |
| `cli`, `diagnostics` | Standalone runner plus non-connecting config, output-directory and bundle inspection commands. |
| `qualification` | Explicit physical qualification helpers. These encode test mechanics, not authorization. |

Dependencies point inward toward `types`, backend protocols and the facade.
User interfaces and transport adapters remain at the outside edge.

## Control and data flow

### Registered workflow

1. At startup, the service loads the workflow JSON and referenced CSV bytes.
2. `workflow_registry` checks configured identity, approval metadata and digest.
3. A client selects only `workflow_id`, expected digest, request UUID and the
   fixed acknowledgement; it cannot upload limits, stages or setpoints.
4. Preflight verifies the current instrument, configuration, bundle and safe
   state. A successful preflight is not a successful test.
5. `workflow_runs` reserves the instrument and calls `execution` with the
   already-owned facade and collector.
6. Normal completion, stop or failure writes terminal evidence and verifies a
   final disabled state, including an independent reconnect on the service path.

Client loss does not stop a service-owned workflow. Start retries use the same
request UUID to avoid duplicate runs. Stop is cooperative and asynchronous;
`stop_requested` is not terminal evidence.
An operator may request early completion of the exact active stage. The
service records the request before asking for optional detail, completes
standby/disable, verifies disabled inactive state, then advances. Run stop and safety
faults retain priority. Reports distinguish an applied operator intervention
from ordinary stage termination even when the run passes.

### Measurements and observability

`acquisition` samples the instrument and owns the measurement CSV. It publishes
copies to bounded observer queues. `runtime()` assembles cached status,
measurement freshness, acquisition health, workflow progress, totals,
interlocks, effective limits and alerts. SSE is allowed to drop display events
for a slow observer and reports `dropped_before`; consumers then refresh
`runtime()`.

UTC timestamps correlate different processes and evidence files. Monotonic
time is used only inside one process for cadence, freshness and expiration.
SSE and browser trends are transient observability, never audit evidence.

### External CAN/BMS data

CAN-PY decodes source data and forwards complete timestamped snapshots on a
dedicated bounded worker. The CAN receive thread never waits for HTTP. One
publisher owns the monotonically increasing sequence for each `source_id`.
Ambiguous transport results retain the exact payload for idempotent retry;
newer data must not overtake it.

Approved workflow rules are part of the immutable bundle. A failure before
start rejects the run. A runtime violation latches its triggering metadata,
requests controlled stop and uses the separately approved emergency fallback
only if the reviewed deadline is missed.

An active stage may also have an ordered list of normal termination conditions
using NHR measurements or fresh BMS signals. The workflow engine checks safety
interlocks first. The first reached condition ends only that stage with a
recorded reason; the routine returns to standby, disables output and proceeds
to the next stage. A following rest keeps acquisition and interlock monitoring
active while stage termination conditions are not evaluated. An external termination
requires a runtime interlock on the same source, signal and unit with a
strictly more extreme threshold. Missing, unhealthy or stale data cannot be
reported as a normal stage completion.

## Safety invariants

- Hardware workflows require an approved exact bundle, resource and serial,
  approved limits, approved stop policy, per-run acknowledgement and separate
  operator authorization.
- Output and watchdog start disabled. Limits are programmed and read back
  before arming or setpoints.
- Active writes require a live arm lease, fresh measurement and safe/fresh
  interlocks.
- Normal completion, failure and interruption converge on zeroed channels,
  disabled output and disabled watchdog, followed by independent verification.
- External transport health, decoding, completeness, units, timestamps and
  freshness are safety inputs; unknown required data is unsafe.
- A browser close, client timeout, HTTP error, SSE EOF or displayed 0 W is not
  proof of electrical isolation. Use a `rest` stage and final readback.
- Software controls complement the NHR emergency stop, fixture protection and
  operator-approved test plan; they do not replace them.

Long CC, CCCV and CP stages are limited to 295 seconds unless the reviewed
workflow enables service-owned arm-lease renewal. Renewable stages remain
capped at 8 hours and sequences at 12 hours. These are absolute ceilings, not
recommended defaults.

An approved workflow may request `post_sequence_rest_s`. The engine appends a
named inactive rest only after every configured stage passes. This relaxation
period remains inside the workflow duration ceiling and live safety monitoring,
and becomes part of the sequence and canonical run evidence. It never delays a
stop or failure path.

## Public service boundary

The versioned API is `/api/v1`. Its endpoint families are:

| Family | Authority |
|---|---|
| Configuration, inventory, status, measurement, acquisition, runtime, events, interlocks | Read-only observation |
| Workflow list, preflight, start, run status, run stop, stage end and reason | Registered workflow authority |
| External snapshot `PUT` | Decoded data publication only; no NHR command authority |
| Controlled service shutdown | Local lifecycle control; exact acknowledgement required; closes every service-owned instrument |
| Connect, detach, limits, arm, command and legacy routine | Compatibility path; physical writes disabled by default |

`primitive_compatibility_control` is a local migration switch, not
authentication or approval. The service and monitor bind to localhost and have
no network authentication boundary; do not expose their ports to another host.

## Evidence model

An active workflow can produce a surveillance acquisition CSV, a finalized
`measurements/session.csv`, `measurements/sequence.csv`, per-stage CSVs,
`run-state.json`, `report.json` and
`artifacts.json`. The artifact manifest names file roles and hashes. The
terminal report plus referenced finalized files are the durable NHR evidence;
live SSE and a still-growing acquisition CSV are not.

CAN-PY retains its source CAN evidence and may create a derived combined
session only after both sources are finalized. A merge failure must preserve
the original CAN and NHR files.

## Design trade-offs

| Choice | Benefit | Cost / consequence |
|---|---|---|
| Persistent 32-bit service | One clear IVI and safety owner; 64-bit integration remains simple | Service lifecycle and uncertain client responses require explicit recovery |
| Immutable local workflow registry | Remote clients cannot inject arbitrary profiles or limits; runs are reproducible by digest | Every reviewed bundle change requires a new digest and service restart |
| Fail-closed external data | Missing or stale BMS protection cannot silently become permissive | Transport/decoder health must be engineered and nuisance stops are possible |
| Asynchronous service runs | Client/UI loss cannot orphan ownership or disable evidence collection | Clients must retain `run_id`, poll terminal state and distinguish timeout from stop |
| Bounded SSE queues | A slow dashboard cannot block acquisition or safety | Display events may be dropped; clients must refresh the snapshot |
| Simulator behind the same facade | Fast deterministic regression coverage of control logic | It cannot prove IVI timing, contactor behavior, wiring or electrical safety |
| Dynamic zero without state change | Avoids relay cycling in signed profiles | 0 A/0 W is not isolation; a `rest` stage is required when isolation matters |
| Explicit post-sequence rest | Captures DUT relaxation in sequence and merged evidence with output disabled | Extends workflow duration and requires CAN/interlock publication until finalization |

## Extension rules

- Add hardware operations through the facade and existing safety primitives,
  never directly in HTTP handlers, clients or UIs.
- Add persistence through `MeasurementSink`; a sink must never block or call
  the instrument.
- Add external sources through decoded snapshots and typed reviewed rules, not
  by importing a CAN stack into the 32-bit service.
- Add displays through `runtime()` and events; a display must not gain write
  routes as a convenience.
- Revisit this document and the [validation plan](VALIDATION.md) before changing
  an authority boundary or adding a new physical capability.
