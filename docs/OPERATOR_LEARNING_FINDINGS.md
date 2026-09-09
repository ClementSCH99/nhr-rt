# Operator learning findings and improvement backlog

## Purpose

This document consolidates the observations made while learning the standalone
runner, localhost service, 64-bit client, workflow registry, evidence files and
live event stream. It distinguishes observed behavior from recommended product
changes. It is not a hardware qualification record and grants no permission to
energize a physical instrument.

The companion [How to operate NHR Remote Testing](HOW_TO_OPERATE_NHR_RT.md)
turns the confirmed behavior into task-oriented examples.

## What the exercises established

### Process and authority model

- The 32-bit service process owns IVI-COM, the physical connection, continuous
  acquisition and workflow execution.
- A 64-bit `NHRServiceClient` sends HTTP requests and observes SSE events. It
  never owns the instrument or the acquisition thread.
- Closing a client console or an SSE stream does not stop the service, an active
  workflow or continuous service acquisition.
- `connect()` initializes the service-owned instrument runtime.
- `disconnect()` is an observer compatibility detach. It does not physically
  disconnect the instrument and does not stop acquisition. The current method
  name is therefore easy to misinterpret.
- A service normally listens on `127.0.0.1:9300`. Starting a second service on
  the same address fails with Windows socket error 10048 and does not disturb
  the first process.
- A controlled service shutdown removes both the listening socket and the
  service-owned process.

### Standalone runner versus service workflow

- `nhr9300-run --simulate` owns and completes one workflow locally. It is the
  shortest path for validating profile behavior and termination logic.
- `nhr9300-service` is persistent. It supports multiple clients, continuous
  acquisition, a local approved workflow registry, remote preflight/start/stop,
  runtime snapshots and SSE observers.
- The service can be idle while remaining useful: it listens for clients and
  acquires only after its service-owned instrument runtime is connected.
- A client can start only a workflow already loaded and approved in the local
  service registry. It cannot upload arbitrary workflow content.

### Workflow identity and digest gates

- `WorkflowBundle.load(profile_path, hardware=False)` is the public way to load
  a bundle. Callers should not instantiate the `WorkflowBundle` dataclass by
  hand.
- `bundle.files` uses canonical logical names such as `workflow.json`; the
  original filename is preserved separately in `source_paths`.
- `bundle_digest` hashes a canonical manifest containing the workflow JSON and
  every referenced dynamic CSV. It is not the same value as a direct-file
  `profile_sha256`.
- The digest participates in three independent checks:

  1. service startup checks the configured expected digest against the local
     bundle;
  2. preflight and start compare the client's digest with the registered
     digest;
  3. the service verifies that the files on disk have not changed since its
     startup snapshot.

- Editing a profile or referenced CSV therefore requires recalculating the
  digest, updating the service configuration, restarting the service and
  repeating preflight.
- A digest proves byte identity, not engineering approval or hardware safety.

### Preflight, execution and stopping

- Preflight is synchronous. It validates the registered bundle, instrument
  identity, initial safe state, safety-limit behavior and final safe state. It
  does not execute the workflow stages or prove their termination behavior.
- Workflow start is asynchronous and returns a run snapshot.
- Cooperative stop is also asynchronous. The observed transition was
  `running -> stop_requested -> stopped`.
- Repeating `stop_workflow()` for the same run is idempotent.
- The client must poll `workflow_run()` until a terminal state:
  `passed`, `stopped`, `failed` or `interrupted`.
- `client.stop()` belongs to the older primitive routine API. It is not the
  correct method for stopping an approved workflow.

### Acquisition, CSVs and evidence

- `acquisition()["sample_count"]` grows while service acquisition is active,
  independently of the lifetime of any one client.
- Acquisition can resume after workflow cleanup and verification reconnect.
  A growing general acquisition CSV does not mean the workflow is still
  running.
- One workflow may legitimately produce several CSV files:

  - a general service-acquisition file;
  - a global workflow sequence file;
  - one file per stage;
  - small rotated service-acquisition files around preflight, reconnect and
    cleanup boundaries.

- The terminal workflow report and its referenced sequence/stage evidence are
  the durable record. SSE events are for live observability only.

### Live events and runtime

- `client.events()` is a blocking iterator. A background thread must iterate
  over it, using an instantiated `threading.Event()` for cancellation.
- At a 5 Hz simulator rate, repeated `workflow`, `interlock`, `limit` and
  `measurement` event groups are expected.
- Closing the SSE subscriber affects only that subscriber.
- If an SSE event reports dropped data, the client should refresh the complete
  `runtime()` snapshot rather than infer missing state.
- Runtime consolidates instrument status, measurement freshness, acquisition,
  active workflow, totals, external sources, interlocks, limits and alerts.

### Confirmed error isolation

- An unknown instrument produces `NHRError: Unknown instrument: ...` without
  stopping another instrument's acquisition.
- A wrong client digest produces `NHRError: Client bundle digest does not match
  the registry` without modifying the registered workflow.
- File drift after service startup produces `Workflow bundle changed after
  service startup; restart required`.
- A client-side error or client exit is not proof that the service or output has
  stopped.

## Defects and confusing behavior reproduced during learning

### P0 — Service simulator ignores the workflow initial voltage

Observed behavior: a service-run constant-current workflow terminated
immediately with a reported voltage of 350 V even though its profile contained
`simulation_initial_voltage_v: 90.0`. The same profile behaved coherently under
`nhr9300-run --simulate`.

Code path:

- `SimulatedBackend` defaults to 350 V in
  `src/nhr9300/backends/simulator.py`;
- the service creates it as `SimulatedBackend(instrument_id)` in
  `src/nhr9300/service.py`, without passing profile or configuration voltage;
- the standalone executor passes the profile's initial voltage in
  `src/nhr9300/execution.py`.

Impact: service-based simulation can falsely satisfy a voltage termination,
produce near-zero duration/energy and report PASS. This makes its functional
voltage evidence unreliable.

Recommended correction:

1. Define an explicit simulator initial-state policy instead of silently using
   350 V. A practical first version is an instrument configuration field such
   as `initial_voltage_v` plus validation against the workflow profile.
2. Decide and document whether simulator state is reset per workflow or
   intentionally preserved between workflows. If both are useful, expose a
   `reset_from_profile` versus `preserve` policy.
3. During simulator preflight, reject a workflow when its declared initial
   voltage conflicts with the selected service simulator policy.
4. Add service-level tests reproducing the 90 V CC termination cases already
   covered by standalone tests.

Acceptance criteria:

- the same approved simulation profile produces equivalent initial voltage,
  termination reason, elapsed time, sample count and approximate Ah/Wh through
  the runner and service;
- a 90 V profile can never terminate at 350 V unless 350 V was explicitly
  selected;
- the chosen reset/preserve behavior is visible in `configuration()` and the
  workflow report.

### P0 — Evidence state writes are fragile in OneDrive folders

Observed behavior: a first run failed with Windows access denied while replacing
`run-state.json.tmp` with `run-state.json`; a later retry passed. This was a
functional persistence failure, not merely test cleanup noise.

Impact: synced-folder scanning or file locks can interrupt a valid workflow and
make repeatability dependent on timing.

Recommended correction:

1. Use a unique temporary filename per write rather than a shared `.tmp` name.
2. Close and flush the file before replacement; keep temporary and destination
   files on the same filesystem.
3. Retry known Windows sharing violations with a short bounded backoff.
4. Preserve fail-closed behavior if the bounded retry expires and report a
   specific evidence-persistence error.
5. Add a startup diagnostic that tests create/write/replace/delete capability
   in `output_dir` without touching hardware.
6. Recommend a local, non-synchronized ASCII path for qualification evidence.

Acceptance criteria:

- deterministic tests inject transient `PermissionError` failures and verify
  successful retry;
- persistent failure produces a terminal safe failure with an actionable error;
- two rapid state writes cannot collide on the same temporary file.

## Prioritized usability and diagnostic improvements

### P1 — Add workflow wait helpers

Every client currently repeats the same polling loop and terminal-state set.
Add a public helper similar to:

```python
run = client.wait_workflow(
    instrument_id="sim-1",
    run_id=run["run_id"],
    timeout_s=60.0,
    poll_interval_s=0.5,
)
```

It should return terminal snapshots, raise a typed timeout without stopping the
service run implicitly, and optionally accept a progress callback. A separate
`stop_and_wait_workflow()` helper could encode cooperative stop correctly.

### P1 — Replace ambiguous lifecycle names

`disconnect()` sounds like it closes the physical connection, but it only
detaches an observer. Add a canonical `detach_observer()` name and retain
`disconnect()` as a deprecated compatibility alias. Likewise, distinguish the
legacy primitive `stop()` as `stop_legacy_routine()` in new documentation and
autocomplete surfaces.

### P1 — Provide typed transport and protocol errors

HTTP service errors become `NHRError`, while connection refusal, socket timeout,
malformed JSON and SSE interruption can surface as lower-level exceptions.
Introduce subclasses such as:

- `NHRTransportError` for service unavailable, DNS/socket and timeout failures;
- `NHRAPIError` with HTTP status and server message;
- `NHRProtocolError` for malformed or unsupported responses;
- `NHRWorkflowTimeout` for client-side wait expiration.

Preserve the original exception as the cause and keep safety wording clear: a
transport error says nothing about physical output state.

### P1 — Make artifact roles explicit

Add an evidence manifest or extend `report.json` so every file has a role,
instrument, run/preflight identifier, lifecycle session and SHA-256. Suggested
roles are `service_acquisition`, `workflow_sequence`, `workflow_stage` and
`preflight`. This would make multiple CSVs self-explanatory and easier to
archive.

Avoid creating empty acquisition CSVs where possible, for example by opening a
sink on the first sample. If rotation is intentional, record its reason.

### P1 — Add a non-energizing doctor command

Add `nhr9300-doctor` or `nhr9300-service --diagnose` that performs only
read-only/software checks by default:

- Python version and process bitness;
- installed package/version and IVI optional dependency availability;
- configuration parsing and resolved paths;
- localhost port ownership and service health;
- registry availability/digest/drift status;
- `output_dir` write-and-atomic-replace capability;
- instrument configuration flags, without connecting or energizing hardware.

Any physical connectivity check must be a separate, explicit and supervised
option.

### P2 — Add configuration and bundle inspection commands

Useful commands would be:

```text
nhr9300-service --check-config path/to/service.json
nhr9300-run --validate --profile path/to/workflow.json
nhr9300-bundle digest path/to/workflow.json
nhr9300-bundle inspect path/to/workflow.json
```

They should print resolved paths, logical bundle members, digest, approvals,
identity and validation errors without running the workflow.

Publishing a JSON Schema for service configuration and workflow profiles would
also improve editor validation and reduce key-name errors.

### P2 — Improve returned state semantics

- Return both the numeric instrument state and its symbolic name, for example
  `state: 1` and `state_name: "STANDBY"`.
- Include `stop_accepted`, `already_requested` or equivalent in idempotent stop
  responses so callers can explain the first and repeated requests.
- In terminal reports, present `outcome: stopped` separately from test
  pass/fail. A deliberate operator stop should not look like an unexplained test
  failure.
- Expose the effective simulator initial-state policy and actual initial voltage
  in runtime and reports.

### P2 — Offer a managed SSE observer

Provide a client utility or context manager that owns its worker thread,
cancellation event, reconnect policy and last sequence number. It should expose
dropped-event detection and instruct callers to refresh `runtime()` after a
gap. The raw blocking generator should remain available for simple clients.

## Documentation corrections incorporated into the How-to

The operator guide explicitly documents the points that caused the most
confusion:

- `run` versus `service`;
- 32-bit service versus 64-bit client requirements;
- service/process/port ownership;
- `connect` and compatibility `disconnect` semantics;
- acquisition lifetime and CSV rotation;
- registry paths, bundle members and all digest checks;
- duration versus termination behavior;
- synchronous preflight versus asynchronous run/stop;
- correct SSE thread cancellation;
- the distinction between live observability and durable evidence;
- known simulator and OneDrive limitations;
- recovery and diagnostics without treating transport loss as safe state;
- separate simulation, preflight and supervised physical-execution gates.

## Suggested delivery order

1. Fix and test service simulator initial-state handling.
2. Harden evidence state persistence on Windows and add output-directory checks.
3. Add client wait/stop helpers and typed transport errors.
4. Add artifact-role manifests and clearer state fields.
5. Add doctor/config/bundle inspection commands and schemas.
6. Add the managed SSE observer after the lower-level contracts are stable.

This order restores trustworthy simulation evidence first, then improves
operational robustness and finally reduces client boilerplate.

## Implementation status (2026-09-09)

The backlog above is retained as the rationale and acceptance record. The
following first compatible implementation is now present:

- service simulations use the explicit `reset_from_profile` policy and expose
  the selected policy and actual initial voltage in configuration/reports;
- workflow state and reports use unique, flushed same-directory temporary
  files with bounded retry of Windows sharing violations;
- `nhr9300-doctor --config ...` checks configuration, bundles, port reachability
  and evidence-directory replacement without connecting an instrument;
- `wait_workflow()` and `stop_and_wait_workflow()` encode terminal polling;
- `detach_observer()` and `stop_legacy_routine()` are the canonical names;
- transport, HTTP API, protocol and workflow timeout failures are typed;
- runtime status includes numeric `state` and symbolic `state_name`, while stop
  replies identify new versus repeated requests;
- each workflow report points to `artifacts.json`, whose entries include role,
  run/instrument identity, size and SHA-256;
- `nhr9300-bundle digest|inspect` validates bundles without executing them;
- `observe_events()` provides a context-managed SSE worker, optional reconnect,
  last-sequence tracking and explicit gap recovery through `refresh_runtime()`.

JSON Schemas and configurable simulator state preservation remain possible
future extensions. The current simulator contract intentionally supports only
`reset_from_profile`, avoiding an ambiguous partially implemented preserve mode.
