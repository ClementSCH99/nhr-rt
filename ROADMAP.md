# NHR Remote Testing roadmap

**Last updated:** August 20, 2026

**Current release:** v0.2.0

**Current phase:** Expansion Phase 2 — remote orchestration, external safety and monitoring

**Target release:** v0.3.0

## Product direction

Version v0.2.0 closed the first expansion phase. The 32-bit IVI service,
`NHR9300` safety facade, simulator and supervised workflow engine now support
the main battery-test routines required for normal bench use. Their software
behavior and the selected physical workflows were validated through Session 5.

Expansion Phase 2 makes those capabilities safely usable by 64-bit
applications. It adds external fail-closed safety inputs and a simple read-only
operator interface without moving hardware authority outside `nhr-rt`.

The target system uses shared authority rather than one universal master:

| Responsibility | Owner |
|---|---|
| IVI connection, approved workflows, arming, NHR limits, interlocks, derating and final safe state | `nhr-rt` 32-bit service |
| CAN capture, DBC decoding and publication of timestamped values | CAN-PY |
| Future orchestration of one combined CAN/NHR session | CAN-PY |
| Initial NHR monitoring interface | Separate 64-bit web monitor supplied by `nhr-rt` |
| Multi-source session manifest and portable CAN/NHR evidence layout | CAN-PY |

CAN-PY is not modified or released by this phase. `nhr-rt` will publish the
external-data contract, examples and a reference snapshot producer so the
integration can later be implemented and validated in the CAN-PY project.

The delivery order intentionally brings runtime observability and the read-only
monitor immediately after remote workflow execution. This provides useful
operator visibility and diagnostic evidence early. External interlocks and SoP
derating follow afterward while the already validated v0.2.0 safety layers
remain mandatory throughout the phase.

## Safety and compatibility boundaries

The following constraints apply to every milestone:

- The 32-bit `nhr-rt` service remains the only process that owns IVI-COM and a
  physical NHR connection.
- All hardware calls continue through `NHR9300`; HTTP clients, interlock
  adapters and the monitor cannot call a backend directly.
- The service remains localhost-only. Remote workflow access is not a network
  authentication boundary.
- Hardware workflows retain the v0.2.0 gates: approved profiles, exact resource
  and serial match, safety-limit readback, watchdog, live arm lease, fresh
  measurements/interlocks, safe cleanup and independent final-state
  verification.
- Service, workflow-registry and rule changes require a restart. Hot reload is
  out of scope.
- Existing v0.2.0 read endpoints remain compatible. Primitive remote hardware
  commands are disabled by default and require an explicit local compatibility
  option.
- No threshold, freshness allowance or fallback delay is assumed to be safe.
  Hardware values must be reviewed and approved before supervised tests.

## Milestone 1 — Freeze the service authority contract

### Outcome

The service has an explicit ownership and compatibility model suitable for
multiple observation clients and future workflow clients.

### Work

1. Document the 32-bit service, 64-bit clients and CAN-PY responsibility split.
2. Introduce versioned `/api/v1` routes while preserving established v0.2.0
   read behavior.
3. Make connection and acquisition ownership service-side. An observer or GUI
   disconnect must not stop another subscriber or disconnect the instrument.
4. Classify existing endpoints as read-only, approved-workflow control or
   primitive compatibility control.
5. Disable `limits`, `arm`, `command` and the original routine endpoint on
   physical instruments unless primitive compatibility control is explicitly
   enabled in the local startup configuration.
6. Define service shutdown, active-workflow shutdown and restart behavior.

### Done when

- the authority model and endpoint classification are documented;
- multiple read-only clients can attach and detach independently in simulation;
- legacy read-only clients remain compatible;
- primitive physical control is rejected by default;
- no new energizing path exists before its contract is reviewed.

## Milestone 2 — Approved workflows through the 64-bit client

### Outcome

A 64-bit program can preflight, start, observe and stop the complete Session 5
workflow lifecycle without receiving authority to define arbitrary hardware
commands.

### Workflow registry

The service loads a local, immutable registry at startup. Each entry identifies:

- a stable `workflow_id`;
- the target instrument;
- the reviewed local workflow profile;
- expected resource and serial identity;
- a bundle digest covering the workflow JSON and referenced dynamic CSV files.

The client requests a known `workflow_id` and the digest it expects. The service
rejects unknown, changed, unapproved or mismatched bundles.

Hardware start uses two independent gates:

1. remote workflow execution is enabled in the local service configuration;
2. the client provides the explicit per-run operator acknowledgement.

### API direction

- `GET /api/v1/instruments/{id}/workflows`
- `POST /api/v1/instruments/{id}/workflow-runs/preflight`
- `POST /api/v1/instruments/{id}/workflow-runs`
- `GET /api/v1/instruments/{id}/workflow-runs/{run_id}`
- `POST /api/v1/instruments/{id}/workflow-runs/{run_id}/stop`

### Work

1. Separate the reusable Session 5 lifecycle from CLI-owned backend creation so
   it can run on the instrument already owned by the service.
2. Add an asynchronous run state machine with one active workflow per
   instrument.
3. Make workflow runs service-owned rather than tied to one HTTP request.
4. Expose current stage, current step, termination target, available progress,
   accumulated Ah/Wh, report location and final result.
5. Make stop idempotent and distinguish a controlled workflow stop from an
   emergency fallback.
6. Extend `NHRServiceClient` with typed methods for the versioned workflow API.
7. Preserve profile, dynamic-input and final-state evidence in the run report.

### Done when

- a separate 64-bit process completes preflight and a simulated multi-stage
  workflow through the service;
- modified workflow or CSV bytes are rejected;
- a second workflow cannot start on the same instrument;
- client loss does not silently transfer hardware ownership;
- stop, failure, interruption and service shutdown all converge on a verified
  safe state.

## Milestone 3 — Runtime observability contract

### Outcome

Read-only applications can obtain one coherent view of a running test without
polling unrelated internal objects or owning the hardware connection.

### Runtime snapshot

The consolidated snapshot includes:

- instrument connection, state and output status;
- latest NHR measurement and freshness;
- acquisition health and evidence path;
- active workflow, stage, step and termination information;
- determinate progress only when the contract supports it;
- charge/discharge Ah and Wh;
- external-source freshness and interlock results;
- effective power limits and active alerts.

When progress cannot be calculated truthfully, the API reports the current
metric and target without inventing a percentage.

### Event stream

Publish versioned, bounded SSE events for:

- `measurement`;
- `workflow`;
- `interlock`;
- `limit`;
- `alert`.

Slow subscribers may lose intermediate display updates but cannot block
acquisition or safety processing. Abnormal acquisition and handler errors
remain observable; normal disconnects do not create error tracebacks.

### API direction

- `GET /api/v1/instruments/{id}/runtime`
- `GET /api/v1/instruments/{id}/events`

### Done when

- snapshot and event schemas are documented and tested;
- multiple clients can observe the same instrument independently;
- reconnect and shutdown remain bounded and interruptible;
- a slow or disconnected viewer cannot affect the workflow.

## Milestone 4 — Read-only 64-bit web monitor

### Outcome

The operator has a simple local page for monitoring an NHR test without using
PowerPanel as the primary live display.

### Work

1. Add a separate `nhr9300-monitor` process running in 64-bit Python and using
   only the public `NHRServiceClient` contract.
2. Keep monitor dependencies optional and separate from the 32-bit IVI service.
3. Serve all web assets locally; no CDN or internet connection is required.
4. Display:
   - voltage, current and power;
   - charge/discharge capacity and energy;
   - NHR state and measurement freshness;
   - active workflow, stage, condition and progress;
   - interlocks, heartbeat, active SoP ceiling and alerts;
   - bounded rolling V/I/P trends.
5. Keep the v0.3.0 interface strictly read-only. It has no connect, disconnect,
   preflight, start, stop, setpoint or emergency-stop control.

### Done when

- the page works offline from a standard 64-bit environment;
- displayed values and alerts match API fixtures and simulator behavior;
- visual QA covers the intended desktop viewport and stale/error states;
- opening, refreshing or closing the monitor does not change NHR state.

## Milestone 5 — External fail-closed interlocks

### Outcome

`nhr-rt` can make start and runtime safety decisions from decoded external data
while CAN-PY remains responsible only for CAN acquisition and decoding.

### External snapshot contract

One snapshot contains:

- `source_id`;
- monotonically increasing sequence number;
- UTC timestamp from the source data, not the forwarding time;
- transport/source health;
- decoded numeric values in documented SI units.

The service records its own monotonic receive time. Cross-process correlation
uses UTC; local freshness and expiry use the service monotonic clock. Missing,
unhealthy, out-of-order, invalid or stale required data fails closed.

### Rule model

Approved workflows declare typed rules rather than arbitrary expressions:

- minimum, maximum or range comparison;
- pre-start, runtime or both;
- required source and signal;
- maximum age;
- optional reviewed stability duration.

Runtime violations latch until the workflow ends. No automatic restart or
automatic interlock reset is allowed.

The first vertical slice proves all three safety classes:

1. an isolation permissive that blocks start;
2. a cell-temperature or pack-voltage trip during a run;
3. loss of a required heartbeat or signal.

The selected response is a controlled stop first. If the instrument does not
reach the defined safe state within a locally configured and validated bound,
`nhr-rt` escalates to `emergency_stop`.

### API direction

- `PUT /api/v1/instruments/{id}/external-sources/{source_id}/snapshot`
- `GET /api/v1/instruments/{id}/interlocks`

### Done when

- rule and snapshot validation pass focused automated tests;
- start is refused with missing, stale or unsafe required inputs;
- runtime loss requests a controlled stop and latches the cause;
- delayed or failed controlled cleanup triggers the bounded emergency fallback;
- reports preserve source identity, freshness, rule results and stop cause.

## Milestone 6 — Dynamic SoP power envelope

### Outcome

Fresh BMS power capability can restrict NHR operation dynamically without
weakening approved static limits.

### Contract

- Charge and discharge power availability are positive magnitudes in watts.
- The effective power constraint is the minimum of the approved workflow
  ceiling, NHR safety limit and fresh external SoP value.
- A new SoP value may raise or lower the effective constraint, but it can never
  exceed the approved ceilings.
- The dynamic envelope changes operating control/limiting values; it does not
  rewrite the approved NHR safety limits.
- Missing, invalid or stale required SoP invokes the same controlled-stop and
  emergency-fallback policy as other external safety data.

### Work

1. Define the power-envelope provider independently of CAN transport.
2. Apply the effective limit to CC, CCCV, constant-power and dynamic-profile
   stages without changing their primary regulation mode.
3. Avoid relay or state cycling for ordinary SoP updates.
4. Record source, requested, approved and applied values for every material
   limit change.
5. Extend the simulator to exercise rising, falling, missing and stale SoP.

### Done when

- simulation demonstrates reduction and restoration of the power ceiling;
- applied setpoints never exceed the workflow, hardware or external ceiling;
- legacy current- and power-regulation tests do not regress;
- stale SoP produces the documented stop behavior and evidence.

## Milestone 7 — Integrated validation and v0.3.0 release

### Software and simulator validation

- registry and bundle-digest validation;
- double-gate and primitive-control rejection;
- workflow job state, exclusivity, stop and restart behavior;
- external value, timestamp, order, freshness and rule validation;
- SoP clamping and regulation-mode compatibility;
- subscriber lifecycle, bounded queues, EOF and reconnect behavior;
- monitor functional and visual QA;
- separate 32-bit service and 64-bit client/monitor process tests;
- full regression suite with environmental cleanup failures reported separately.

### Supervised physical validation

Physical work remains gated by reviewed profiles, an operator at the bench and
an accessible emergency stop. The intended sequence is:

1. remote preflight without energizing;
2. short approved workflow launched by the 64-bit client;
3. unsafe isolation permissive blocking start;
4. temperature or voltage trip requesting controlled stop;
5. measurable SoP reduction under approved static limits;
6. heartbeat loss and bounded emergency fallback;
7. simultaneous web monitoring without interference;
8. cleanup plus independent reconnect confirming output off, watchdog off and
   all channels/setpoints disabled or zeroed.

Evidence includes exact profiles and digests, external snapshots, event
chronology, workflow reports, CSVs, monitor captures and independently observed
final state.

### Release gate

Version v0.3.0 is published only after:

- architectural review;
- explicit approval of profiles, thresholds and timing values;
- automated and supervised acceptance evidence;
- operator confirmation of physical behavior;
- documentation and archive review;
- separate authorization to commit, tag, push and publish the release.

## Explicit limitations

- The reference 64-bit snapshot producer validates the `nhr-rt` boundary, but
  does not prove the real DBC → CAN-PY → BMS chain.
- Real CAN integration and any CAN-PY branch work are planned and validated in
  the CAN-PY project.
- CAN temperature is not accepted as a real interlock until signal identity,
  units, timestamps, min/max aggregation and the physical sensor chain are
  verified.
- The initial monitor does not control tests. GUI-based control is considered
  only after the read-only interface and remote workflow API have accumulated
  representative field evidence.
- PowerPanel remains available for vendor diagnostics and independent checks
  until replacement behavior is validated and formally accepted.

Completed v0.2.0 behavior and evidence remain indexed in the
[validation record](docs/VALIDATION.md) and
[archive registry](archives/README.md).
