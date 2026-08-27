# Expansion Phase 2 physical validation and learning plan

> Archived execution contract. The campaign is complete; generated evidence
> and approved local profiles were removed during closeout. See [archive
> notes](README.md).

**Status:** finalized; Tier S ready, physical tiers gated

**Responsibility revision:** 2026-08-25

**Target:** `nhr-rt` v0.3.0 Expansion Phase 2, Milestones 1 to 4

**Hardware:** NHR9300 logical resource `DC PM 1`, exact identity to be confirmed

**Energizing DUT:** battery module, operating envelope not yet defined
**Execution model:** assistant-executed, operator-supervised, authorization-tiered

## 1. Purpose

This document separates two complementary goals:

1. physically validate the new service authority, approved-workflow,
   observability and read-only monitor behavior added after v0.2.0 efficiently,
   with the assistant executing software and approved bench commands;
2. build operator proficiency through a separate hands-on exercise track in
   which the operator executes and the assistant coaches.

The validation campaign prioritizes repeatability, evidence quality and rapid
diagnosis. The learning track prioritizes understanding and deliberate manual
practice. Exercise completion is not automatically accepted as campaign
evidence; a campaign test must still use its approved configuration, gates and
record.

The campaign deliberately starts without an energized DUT. It then exercises a
physical `rest` workflow before any battery module is connected. Energizing
tests remain blocked until the module operating envelope, fixture limits,
workflow profile and controlled-stop policy have been reviewed and explicitly
approved.

This document is a plan and execution record template. It is not by itself an
authorization to connect hardware, energize an output, change an NHR setting or
perform a Git operation.

## 2. Scope

### Included

- separate 32-bit service and 64-bit client/monitor environments;
- physical IVI identity, status, measurement and acquisition;
- service-owned connection and acquisition;
- independent observer attach/detach;
- rejection of physical primitive compatibility commands;
- immutable workflow registry and bundle digest;
- physical preflight without workflow start;
- a non-energizing service-owned `rest` workflow;
- runtime snapshot and bounded event observation;
- read-only web monitor behavior;
- controlled workflow stop, client loss and service shutdown;
- later low-energy validation with an approved battery module envelope;
- final cleanup and independent safe-state verification.

### Excluded from this campaign

- Milestone 5 external CAN/BMS fail-closed interlocks;
- Milestone 6 dynamic SoP power limiting;
- real CAN-PY integration;
- treating the monitor as a safety controller or durable evidence recorder;
- approving battery limits, stop delays or fallback timing by inference;
- release, tag, merge, push or publication without separate authorization.

The runtime fields reserved for external sources and dynamic SoP are expected
to report `not_configured` throughout Milestones 1 to 4.

## 3. Authority and working method

### Operator responsibilities

The operator:

- controls physical access to the bench and emergency stop;
- confirms the wiring, fixture and NHR state before every physical gate;
- performs physical manipulations and independent vendor-panel checks;
- defines and approves the battery module operating envelope;
- reviews profiles, limits and the controlled-stop timing before energization;
- supervises assistant-executed physical tests and may stop them at any time;
- validates that software measurements represent physical reality;
- reports observations that software cannot establish;
- gives explicit approval immediately before each energizing test;
- gives separate authorization for any Git operation.

### Assistant responsibilities

The assistant:

- inspects the repository and executes software, simulator and evidence checks
  autonomously within the approved workspace;
- presents a concise test card before each physical test, including purpose,
  actions, expected transitions, stop criteria and required operator gate;
- executes coherent test blocks after the applicable authorization rather than
  asking the operator to copy routine commands;
- captures console output, HTTP responses, snapshots, CSVs and JSON reports;
- distinguishes software evidence from physical/operator evidence;
- stops on failed gates, unexpected physical behavior or operator request;
- performs the already-authorized safe cleanup for an active test without
  waiting for another approval when delay would increase risk;
- proposes corrections without expanding hardware authority or performing Git
  actions autonomously;
- prepares a concise validation summary after each accepted test.

### Authorization tiers

| Tier | Scope | Assistant authority | Required operator gate |
|---|---|---|---|
| S | Repository inspection, environments, automated tests, simulator, local monitor, evidence parsing | Autonomous | Campaign/task scope confirmed; no Git |
| P | Physical connection, identity, read-only status/measurement/acquisition, observers and monitor | Execute after gate | Test-specific bench state, identity expectation, output/watchdog off and operator present |
| N | Non-energizing physical preflight, approved `rest`, controlled stop and service-loss cleanup | Execute after gate | Reviewed bundle/policy, emergency-stop access and explicit test approval |
| E | Any arm, setpoint, output enable or energy transfer | Execute only for the approved run | Completed C0 plus immediate explicit approval before every start |
| G | Branch, commit, merge, tag, push or publication | None by default | Separate explicit Git authorization |

Tier S is autonomous. Tier P may be approved as a bounded block of named tests
while the operator remains present and the physical configuration is unchanged.
Tier N is approved per named test, and Tier E per individual run immediately
before start. No tier authorizes changed profiles, changed limits or a higher
tier. Read-only Git commands remain outside Tier S and require explicit operator
authorization; Git mutation requires its own separate and specific approval.

### Supervised test loop

Every test follows the same loop:

1. **Inspect:** the assistant confirms code, configuration, processes and prior
   evidence without changing hardware or Git state.
2. **Brief:** the assistant presents the complete test card and authorization
   tier; the operator challenges assumptions and confirms physical readiness.
3. **Gate:** obtain the exact approval required for the named test.
4. **Execute:** the assistant runs the approved command sequence while the
   operator supervises the bench and reports physical observations.
5. **Observe:** both parties compare console/API evidence with independent
   physical reality; the assistant preserves durable artifacts.
6. **Decide:** mark `PASS`, `FAIL`, `BLOCKED` or `REPEAT`.
7. **Record:** complete the test record and state what remains authorized before
   moving forward.

Routine software checks may be batched. Physical gates, unexpected results and
energizing starts are never batched across tests.

No failed gate is skipped merely because a later test appears able to cover it.

## 4. Global safety rules

The following rules apply to the complete campaign:

- An accessible physical emergency stop and operator presence are mandatory for
  every energizing action.
- The exact IVI logical resource and NHR serial number must match the approved
  configuration.
- Physical output and watchdog must initially be disabled.
- Repository example profiles are unapproved and must never be used directly
  for hardware activation.
- Safety limits must be programmed and read back before arm or setpoints.
- `primitive_compatibility_control` remains `false` for the new workflow path.
- A client timeout, closed browser, HTTP error or SSE EOF is never proof that
  the physical output is safe.
- A final safe state requires independent observation: output disabled,
  watchdog disabled, channels disabled and setpoints zeroed.
- Active `0 W` is not electrical isolation. Use a `rest` stage whenever
  isolation is required.
- No battery value or controlled-stop timeout is assumed safe. Unknown values
  remain `TBD — NOT APPROVED`.
- If the physical state is uncertain, use the physical emergency stop and
  verify locally before attempting software recovery.

## 5. Campaign prerequisites and gate ownership

### Before Tier S / Test T0

The operator confirms the campaign scope and authorizes assistant execution in
the workspace. The assistant then verifies:

- Windows host and a usable 32-bit Python environment for IVI-COM;
- a separate 64-bit Python 3.12 environment for the client and monitor;
- editable package installation and dependency separation;
- ability to identify and stop Python, service and monitor processes;
- writable evidence locations and simulator configuration;
- automated-test and simulator baseline.

Branch, commit and working-tree state remain required traceability inputs.
Because Git is outside Tier S, the operator either supplies them or explicitly
authorizes the assistant to run the necessary read-only Git commands. Tier S
does not authorize any branch, commit, merge, tag, push or publication.

### Before a Tier P block / Tests A1-A6

The assistant verifies the exact local service configuration and presents the
bounded list of Tier P tests. The operator confirms:

- no battery module is connected and no DUT is energized;
- exact IVI logical resource and expected NHR serial;
- output and watchdog independently observed disabled;
- emergency stop accessible and operator continuously present;
- PowerPanel or another approved independent view is available;
- no process other than the service will own IVI during execution.

One approval may cover a named A1-A6 block while these conditions remain
unchanged. Any identity mismatch, configuration change, unexplained physical
state, operator absence or service restart outside the planned sequence expires
the block authorization.

### Before Tier N / Tests B1-B5

For each named test, the assistant prepares the artifacts and the operator
reviews and approves:

- a physical `rest` workflow containing no energizing stage;
- immutable workflow ID and exact bundle digest;
- registry entry and exact resource/serial identity;
- `primitive_compatibility_control: false` and only the required remote
  workflow path enabled;
- physical controlled-stop policy and emergency-stop availability.

The assistant may compute, compare and preflight artifacts, but cannot approve
profile intent, identity or physical stop timing on the operator's behalf.

### Before Tier E / Tests C0-C6

C0 must convert all `TBD — NOT APPROVED` items into an operator-approved
hardware contract, including:

- module manufacturer, configuration, SOC and temperature window;
- nominal/minimum/maximum voltage;
- allowed charge/discharge current and power;
- capacity/energy assumptions and termination conditions;
- fixture, fuse, contactor, cabling and polarity review;
- profile duration, controlled-stop bound and watchdog/fallback expectation;
- verified measurement sources and physical emergency-stop check.

The assistant checks consistency, builds the evidence package and performs
preflight. The operator owns the approved values and gives a new immediate
authorization before every Tier E run. Approval expires after the run, any
profile/configuration change or any failed gate.

## 6. Evidence ownership and traceability

Create one campaign identifier before execution:

```text
PHASE2-PHYSICAL-YYYYMMDD-NHR79503
```

The assistant owns evidence capture, indexing and software interpretation. The
operator owns independent physical observations and confirms whether displayed
measurements represent the bench. For each test preserve, as applicable:

- date/time and test ID;
- repository branch and commit;
- exact 32-bit and 64-bit Python versions/architectures;
- service configuration with secrets or local-only data removed if necessary;
- workflow JSON, referenced CSV files and bundle digest;
- resource, serial and independent initial-state observation;
- authorization tier, exact scope, gate text and approval time;
- commands executed by the assistant;
- physical actions and independent observations reported by the operator;
- complete console output;
- preflight/start/run/stop API responses;
- runtime snapshots and relevant SSE chronology;
- workflow JSON report and acquisition CSV paths;
- monitor screenshot when it adds useful display evidence;
- PowerPanel or other independent final-state observation;
- assistant assessment, operator confirmation and final disposition.

Browser trends and screenshots support diagnosis but do not replace the
acquisition CSV or workflow report. Files are not moved, renamed or edited
while a service or workflow may still be writing them.

### Evidence workflow

1. The assistant creates the campaign/test index and records the approved scope.
2. Command output is captured directly when practical; otherwise the exact
   terminal output is preserved with timestamps and process identity.
3. The assistant resolves final CSV/report paths from the owning service, not
   from guessed filenames.
4. The operator supplies physical observations at initial, transition and final
   gates.
5. The assistant reconciles API/runtime/events with CSV/report evidence and
   highlights every unexplained difference.
6. Tier S results may be disposed by the assistant. Tier P/N/E cannot receive
   `PASS` until the operator confirms the required physical evidence.

### Test disposition

Use exactly one disposition:

- `PASS`: every acceptance criterion and final-state check passed;
- `FAIL`: an expected behavior was contradicted;
- `BLOCKED`: a prerequisite, tool or approved value is missing;
- `REPEAT`: evidence is incomplete or a controlled procedural error occurred.

The assistant may assign `FAIL` or `BLOCKED` immediately when a software or
safety criterion is contradicted. After a software, configuration, registry or
profile change, the affected test and all downstream safety-dependent tests
return to `BLOCKED` until the relevant regressions and gates pass again.

## 7. Test sequence and default execution ownership

| Order | ID | Test | Tier | Default execution | Energy state | Milestone |
|---:|---|---|---|---|---|---|
| 0 | T0 | Environments and software baseline | S | Assistant autonomous | No hardware command | Foundation |
| 1 | A1 | Physical connection and acquisition | P | Assistant; operator supervises bench | Output disabled | M1/M3 |
| 2 | A2 | Independent observers | P | Assistant; operator supervises bench | Output disabled | M1/M3/M4 |
| 3 | A3 | Primitive-control rejection | P | Assistant after special gate | Output disabled | M1 |
| 4 | A4 | Runtime/independent consistency | P | Assistant + operator comparison | Output disabled | M3/M4 |
| 5 | A5 | Monitor loss isolation | P | Assistant; operator supervises bench | Output disabled | M1/M4 |
| 6 | A6 | Graceful service shutdown | P | Assistant + operator final check | Output disabled | M1 |
| 7 | B1 | Registry, digest and physical preflight | N | Assistant after artifact approval | No workflow start | M2 |
| 8 | B2 | Service-owned `rest` workflow | N | Assistant after per-test gate | Non-energizing | M2/M3/M4 |
| 9 | B3 | Controlled stop during `rest` | N | Assistant after per-test gate | Non-energizing | M2 |
| 10 | B4 | Client loss during bounded `rest` | N | Assistant after per-test gate | Non-energizing | M1/M2 |
| 11 | B5 | Service shutdown during `rest` | N | Assistant after per-test gate | Non-energizing | M1/M2 |
| 12 | C0 | Battery module/profile approval | E gate | Operator approves; assistant checks | No command | Safety gate |
| 13 | C1 | Short approved workflow | E | Assistant after immediate run approval | Energizing | M2 |
| 14 | C2 | Simultaneous monitor observation | E | Assistant after immediate run approval | Energizing | M3/M4 |
| 15 | C3 | Controlled stop under energy | E | Assistant after immediate run approval | Energizing | M2 |
| 16 | C4 | Client loss under energy | E | Assistant after immediate run approval | Energizing | M1/M2 |
| 17 | C5 | Graceful service shutdown under energy | E | Assistant after immediate run approval | Energizing | M1/M2 |
| 18 | C6 | Abrupt loss/watchdog fallback | E | Assistant after separate loss approval | Energizing | Final resilience |
| 19 | Z1 | Campaign closeout | P | Assistant evidence + operator final check | Output disabled | Release evidence |

The table states default ownership, not standing authorization. Section 5 gates
and the test card below control actual execution.

## 8. Detailed tests

Each card identifies its execution tier and the evidence that only the operator
can supply. Unless explicitly assigned to the operator, commands, process
control, API calls and evidence capture are executed by the assistant. Physical
manipulations, emergency-stop control, vendor-panel observations and approval
of battery/profile limits remain with the operator.

### T0 — Environments and software baseline

**Tier / responsibility:** Tier S. The assistant executes the complete software
baseline autonomously. The operator supplies repository identity unless
read-only Git access is separately authorized.

**Purpose:** establish a reproducible two-process software baseline before
physical access.

**Execution sequence:**

1. Record branch, commit and working-tree status using read-only Git commands.
2. Verify that `.venv32` reports a 32-bit interpreter.
3. Create or verify a separate 64-bit Python environment.
4. Install the editable IVI/service package in 32-bit Python.
5. Install the base client/monitor package in 64-bit Python.
6. Run the approved software regression command.
7. Start the simulator service from `examples/service.simulator.json`.
8. Query inventory and runtime from a 64-bit client.
9. Start the monitor and open its localhost page.
10. Stop the monitor and simulator service cleanly.

**Expected results:**

- architectures report 32 and 64 bits as intended;
- the software suite passes with only explicitly marked hardware skips;
- the 64-bit process uses HTTP and never imports IVI-COM;
- the simulator produces fresh acquisition and runtime state;
- opening/closing the monitor does not change simulator state.

**Training handoff:** operator explain-back belongs to exercises L0-L1 and does
not block the Tier S campaign disposition.

### A1 — Physical connection and acquisition

**Tier / responsibility:** Tier P. The assistant starts the service, connects
the client and captures evidence. The operator confirms initial/final physical
state, identity and absence of unintended transition.

**Purpose:** verify the service-owned physical connection and continuous
read-only acquisition without an energized DUT.

**Preconditions:**

- NHR output and watchdog independently observed disabled;
- no battery module connected;
- `operator_supervised`, `primitive_compatibility_control` and
  `remote_workflow_control` remain `false`;
- logical resource and expected serial available for comparison.

**Execution sequence:**

1. Inspect the exact local JSON configuration.
2. Start the 32-bit physical service.
3. Connect one 64-bit `NHRServiceClient` observer.
4. Read configuration, inventory, status, measurement, acquisition and runtime.
5. Let acquisition run long enough to establish a stable observed rate.
6. Inspect the absolute acquisition CSV path and a small sample of rows.

**Acceptance criteria:**

- resource and serial match;
- service reports connected state and output disabled;
- measurement becomes available and fresh;
- acquisition is active with increasing sample count;
- observed rate is compatible with the configured rate;
- timestamps are UTC and monotonic age is plausible;
- no acquisition error or unexpected alert exists;
- physical output/watchdog remain disabled.

**Stop immediately if:** identity differs, output is enabled, PowerPanel shows a
conflicting state, acquisition fails, or any unexplained physical transition
occurs.

### A2 — Independent observers

**Tier / responsibility:** Tier P. The assistant manages all client/monitor
processes. The operator watches for any physical change and confirms final
output/watchdog state.

**Purpose:** prove that observer ownership is independent from service hardware
ownership.

**Execution sequence:**

1. Keep the service and first client running.
2. Attach a second 64-bit client and one monitor process.
3. Confirm all three observers receive coherent data.
4. Close the second client.
5. Confirm the first client, monitor and acquisition continue.
6. Close the first client.
7. Confirm the monitor and service acquisition continue.
8. Reattach a client and verify the sample count has continued increasing.

**Acceptance criteria:**

- detaching any observer affects only that observer;
- no detach closes IVI or stops acquisition;
- remaining observers retain fresh data;
- reattachment succeeds without resetting the evidence path unexpectedly;
- output and watchdog remain disabled.

### A3 — Physical primitive-control rejection

**Tier / responsibility:** Tier P with a special request gate. The assistant
selects and sends the reviewed request only after the two policy checks and
operator confirmation.

**Purpose:** confirm that the physical service rejects legacy primitive state
changes before they reach the backend.

**Special gate:** do not prepare or send the rejection request until both the
local file and `GET /api/v1/configuration` independently report
`primitive_compatibility_control: false`.

**Execution sequence:**

1. Verify the policy twice as described above.
2. Verify output/watchdog disabled.
3. The assistant sends one reviewed primitive compatibility request.
4. Record the HTTP response and immediately reread runtime and physical state.

The exact endpoint and payload are selected during execution. They are not
embedded in this standing plan because an accidentally enabled compatibility
flag could otherwise turn a rejection test into a real state change.

**Acceptance criteria:**

- response is HTTP 403 with a policy error;
- connection and acquisition remain active;
- no limit, arm, setpoint, output, watchdog or operating-state change occurs;
- no unexpected backend/IVI error is logged.

### A4 — Runtime and independent-state consistency

**Tier / responsibility:** Tier P, shared comparison. The assistant captures
runtime/CSV evidence; the operator supplies the simultaneous PowerPanel or
approved independent observation.

**Purpose:** validate the consolidated runtime contract against an independent
physical view.

**Execution sequence:**

1. Capture one `/runtime` snapshot.
2. Compare connection, operating state, output, watchdog and V/I/P with
   PowerPanel or another approved independent view.
3. Compare runtime measurement values with the latest acquisition CSV row,
   allowing for their distinct timestamps/cadences.
4. Verify acquisition health, sample count and evidence path.
5. Verify workflow is idle and external/SoP fields are `not_configured`.

**Acceptance criteria:**

- no material unexplained disagreement exists;
- freshness and timestamps explain ordinary small display differences;
- unavailable values remain unavailable rather than being displayed as zero;
- no progress percentage is invented while idle;
- alerts reflect the actual state.

### A5 — Monitor loss isolation

**Tier / responsibility:** Tier P. The assistant controls browser/monitor/client
processes and captures continuity evidence; the operator confirms no physical
transition.

**Purpose:** prove that the read-only monitor has no authority over service or
hardware lifecycle.

**Execution sequence:**

1. Start the monitor and confirm live values.
2. Close only the browser tab.
3. Confirm service acquisition and another client continue.
4. Reopen the page and confirm recovery.
5. Stop only the monitor process.
6. Confirm service acquisition and physical state remain unchanged.
7. Restart the monitor and confirm it resumes from current runtime state.

**Acceptance criteria:**

- browser/monitor loss causes no NHR state change;
- monitor recovery does not reconnect or reset the physical instrument;
- no control affordance or successful write method is exposed;
- trends may restart, but durable acquisition continues uninterrupted.

### A6 — Graceful service shutdown with output disabled

**Tier / responsibility:** Tier P. The assistant performs normal service stop,
captures cleanup timing/logs and restarts only as planned. The operator performs
the independent final-state check before restart.

**Purpose:** physically validate bounded service cleanup without an active
workflow or energized output.

**Execution sequence:**

1. Record the current runtime and acquisition path.
2. Stop the service normally from its console.
3. Observe service cleanup messages.
4. Use PowerPanel or a separate read-only connection to verify final state.
5. Restart the service and confirm fresh acquisition resumes.

**Acceptance criteria:**

- service disables output and watchdog before closing;
- final independent state is output off, watchdog off, channels disabled and
  setpoints zero;
- service process exits without hanging;
- restart creates a fresh service lifecycle and usable acquisition;
- no software three-second bound is accepted as physical timing evidence until
  an observed and reviewed bound is recorded.

### B1 — Registry, digest and physical preflight

**Tier / responsibility:** Tier N. The assistant prepares registry/digest
evidence and runs preflight after the operator approves the exact non-energizing
bundle, identity and controlled-stop policy. No workflow start is authorized.

**Purpose:** validate the immutable approved-workflow contract without starting
a workflow.

**Preconditions:** reviewed `rest` profile, registry entry, exact identity and
controlled-stop policy. The profile contains no energizing stage.

**Execution sequence:**

1. Inspect the workflow JSON and approvals.
2. Compute the bundle digest using `WorkflowBundle.load(..., hardware=True)`.
3. Compare the digest to `expected_bundle_digest` in the local service config.
4. Confirm `primitive_compatibility_control: false`.
5. Enable only the reviewed remote workflow path and restart the service.
6. List registered workflows from the 64-bit client.
7. Run preflight using the exact workflow ID and digest.
8. Record identity, limits/readback and final preflight result.
9. Optionally perform negative preflights with an unknown ID or incorrect
   digest, without changing the approved files.

**Acceptance criteria:**

- exact bundle appears once with the expected digest;
- valid preflight passes without starting a run or enabling output;
- unknown/mismatched requests are rejected;
- any disk drift is rejected until restart;
- output and watchdog remain disabled after every preflight.

### B2 — Service-owned physical `rest` workflow

**Tier / responsibility:** Tier N. The assistant executes the approved run and
collects lifecycle evidence. The operator confirms emergency-stop readiness,
gives the per-test approval and independently verifies output remains disabled.

**Purpose:** exercise the complete remote workflow lifecycle on physical
hardware without intentional energy transfer.

**Execution sequence:**

1. Repeat the initial-state and emergency-stop checks.
2. Repeat preflight and review its output.
3. The assistant supplies the per-run acknowledgement only after operator
   confirmation.
4. Start the registered `rest` workflow with a new UUID request ID.
5. Poll the run while separately observing runtime and monitor state.
6. Record state transitions, stage, elapsed progress and event chronology.
7. Allow the run to finish normally.
8. Inspect report, acquisition CSV and service-owned verification reconnect.
9. Independently verify final safe state.

**Acceptance criteria:**

- states progress through accepted/preflight/running/passed as documented;
- only the reviewed `rest` stage executes;
- output remains disabled throughout;
- progress is based on reviewed duration;
- monitor and client agree on run identity/state;
- final report identifies exact profile, digest, termination and evidence;
- independent reconnect confirms the complete safe state.

### B3 — Controlled stop during `rest`

**Tier / responsibility:** Tier N. The assistant starts and stops the approved
run and measures software chronology. The operator confirms physical safe-state
convergence.

**Purpose:** validate run-owned, idempotent cooperative stop before testing it
under energy.

**Execution sequence:**

1. Start a sufficiently long reviewed `rest` workflow.
2. Confirm it is running and output remains disabled.
3. Request stop using the run-specific endpoint.
4. Repeat the same stop request once.
5. Measure and record the observed transition to a terminal state.
6. Inspect report, stop reason and independent final state.

**Acceptance criteria:**

- first stop request is accepted;
- repeated stop does not create another action or error;
- terminal state is `stopped`, not silently `passed` or `failed`;
- no emergency fallback is used when cooperative stop succeeds;
- physical safe state is independently confirmed.

### B4 — Client loss during bounded `rest`

**Tier / responsibility:** Tier N. The assistant controls the initiating and
observer clients while the operator maintains physical supervision.

**Purpose:** confirm that the service, not the initiating HTTP client, owns an
active run.

**Execution sequence:**

1. Start a short, self-terminating `rest` workflow.
2. Record `run_id` and `request_id`.
3. Close only the initiating 64-bit client process.
4. Continue observing through the monitor or a second client.
5. Reconnect a client and retrieve the same `run_id`.
6. Allow normal completion and verify evidence/final state.

**Acceptance criteria:**

- client loss neither aborts nor duplicates the workflow;
- service retains the same run identity;
- another observer can follow current state;
- reconnect retrieves the original run;
- workflow ends normally and safely.

### B5 — Service shutdown during `rest`

**Tier / responsibility:** Tier N. The assistant performs the planned normal
service shutdown and recovery checks. The operator confirms the independent
physical state before any restart.

**Purpose:** validate service-owned interruption and cleanup while a physical,
but non-energizing, workflow owns the instrument.

**Execution sequence:**

1. Start a reviewed long `rest` workflow.
2. Confirm run identity and output disabled.
3. Request normal service shutdown from its console.
4. Record cooperative stop, fallback decisions, acquisition shutdown and IVI
   close messages.
5. Independently verify final physical state.
6. Restart the service and inspect recovery of the non-terminal manifest.
7. Run a fresh preflight before allowing another workflow.

**Acceptance criteria:**

- shutdown requests workflow stop before closing resources;
- no output is enabled at any point;
- interrupted/recovered state is explicit rather than silently discarded;
- new runs remain blocked until required recovery/preflight completes;
- final physical state is independently safe.

### C0 — Battery module and profile approval gate

**Tier / responsibility:** Tier E approval gate. The assistant structures the
contract, checks consistency and identifies missing evidence. The operator owns
every proposed physical value and signs each approval. No hardware command is
authorized by C0 itself.

**Purpose:** convert module/fixture information into an explicitly reviewed
hardware contract. No energizing command is allowed in this test.

Complete and approve this table during execution:

| Item | Proposed value | Evidence/source | Approved by/date |
|---|---:|---|---|
| Module configuration | TBD — NOT APPROVED |  |  |
| Initial SOC range | TBD — NOT APPROVED |  |  |
| Initial temperature range | TBD — NOT APPROVED |  |  |
| Minimum voltage | TBD — NOT APPROVED |  |  |
| Maximum voltage | TBD — NOT APPROVED |  |  |
| Maximum charge current | TBD — NOT APPROVED |  |  |
| Maximum discharge current | TBD — NOT APPROVED |  |  |
| Maximum charge power | TBD — NOT APPROVED |  |  |
| Maximum discharge power | TBD — NOT APPROVED |  |  |
| Maximum stage duration | TBD — NOT APPROVED |  |  |
| Capacity/energy termination | TBD — NOT APPROVED |  |  |
| Controlled-stop bound | TBD — NOT APPROVED |  |  |
| Watchdog/fallback expectation | TBD — NOT APPROVED |  |  |
| Fixture/fuse/cable ratings | TBD — NOT APPROVED |  |  |
| Emergency-stop check | TBD — NOT APPROVED | Physical check |  |

The first energizing profile should be intentionally short, low energy,
unidirectional and end in `rest`. Charge and discharge should be introduced as
separate review steps unless the fixture and module plan explicitly justify a
combined sequence.

### C1 — Short approved energizing workflow

**Tier / responsibility:** Tier E. The assistant performs preflight and the
approved run only after immediate operator authorization. The operator controls
the bench/emergency stop and validates independent measurements continuously.

**Purpose:** validate the approved remote workflow path with the smallest
reviewed battery energy exposure.

**Gate:** exact commands are written only after C0 is complete and the operator
gives immediate authorization with emergency-stop access confirmed.

**Procedure outline:**

1. Verify wiring, polarity, SOC, temperature, fixture and emergency stop.
2. Verify initial NHR output/watchdog disabled.
3. Verify exact profile, digest, registry, resource and serial.
4. Run physical preflight and review every readback.
5. Start the short workflow with a new request UUID and acknowledgement.
6. Observe NHR, independent measurement and software runtime continuously.
7. Allow the reviewed termination condition to stop the stage.
8. Require final `rest`, cleanup and independent reconnect verification.

**Acceptance criteria:** to be finalized from the approved module envelope.
At minimum, applied values never exceed approved limits, termination occurs for
the expected reason, evidence is complete and final safe state is verified.

### C2 — Simultaneous monitor observation under energy

**Tier / responsibility:** Tier E. The assistant repeats only the exact C1
profile and changes observation load after a new immediate approval. The
operator maintains the same physical supervision as C1.

**Purpose:** show that monitor and additional observers do not materially
interfere with an energized workflow.

Repeat the exact approved C1 profile, changing only the observation load:

- one initiating client;
- one monitor page;
- optionally one second read-only client.

Compare acquisition cadence, stage duration, termination, alerts and final
state with C1. The monitor must not be accepted as the independent safety view.

### C3 — Controlled stop under energy

**Tier / responsibility:** Tier E. The assistant executes the preplanned stop
and measures chronology. The operator independently identifies the physical
safe-state time and triggers emergency fallback if the approved bound is missed.

**Purpose:** physically validate cooperative stop timing and safe-state
convergence using the approved battery envelope.

Start a bounded profile that remains within C0 limits, request run-specific
stop at a planned point, and measure from request receipt to independently
verified safe state. The approved bound and escalation behavior must be decided
before start. Unexpected delay, state, current or contactor behavior is a
campaign stop.

### C4 — Client loss under energy

**Tier / responsibility:** Tier E. The assistant terminates only the named
client process and preserves the service/run evidence. The operator maintains
continuous local supervision.

**Purpose:** prove that loss of the initiating 64-bit process does not transfer
or abandon hardware authority.

Use a very short self-terminating approved profile. Close only the initiating
client, continue local supervision, reconnect, retrieve the same run and verify
normal termination. Do not combine this test with service loss.

### C5 — Graceful service shutdown under energy

**Tier / responsibility:** Tier E. The assistant requests only normal service
shutdown and captures cleanup. The operator confirms independent physical safe
state before any recovery process starts.

**Purpose:** validate the complete service shutdown contract during an active
approved workflow.

This test occurs only after C3 passes. Use normal service shutdown, not process
termination. Record cooperative stop timing, any fallback, output/watchdog
readback, acquisition closure, IVI close and independent final state.

### C6 — Abrupt loss and watchdog fallback

**Tier / responsibility:** Tier E with a separate loss-mechanism approval. The
assistant executes only the reviewed loss/recovery sequence. The operator owns
emergency fallback and independent final-state verification.

**Purpose:** validate the final resilience behavior under controlled low-energy
conditions.

This is the last and highest-risk test. It requires a separately reviewed loss
mechanism, recovery plan, watchdog expectation and emergency fallback. Close
PowerPanel if required by the reviewed loss scenario so it cannot keep the
communication path alive. Do not improvise process termination during the
test. Evidence must be written before any potentially blocking loss action,
and recovery must use an independent process/view.

### Z1 — Campaign closeout

**Tier / responsibility:** Tier P. The assistant closes processes, resolves and
indexes evidence, checks completeness and drafts the campaign disposition. The
operator supplies the final independent NHR/DUT observation and confirms the
physical setup is left safe.

**Execution sequence:**

1. Confirm no workflow is active and preserve the final runtime/acquisition
   state.
2. Perform the approved normal service cleanup.
3. Confirm service, monitor and client processes are closed and ports released.
4. Resolve every final CSV/report path and complete the test matrix.
5. Independently verify output off, watchdog off, channels disabled and
   setpoints zero.
6. Record deviations, exclusions, unresolved risks and readiness recommendation.

**Acceptance criteria:**

- no active/orphaned process or uncertain IVI owner remains;
- durable evidence is indexed and internally consistent;
- independent final safe state is confirmed by the operator;
- software-only, simulator and physical evidence are clearly separated;
- the final recommendation does not claim readiness beyond tested milestones,
  profiles, limits or physical configuration.

## 9. Campaign-wide stop criteria

Stop the current test and do not advance if any of the following occurs:

- identity or configuration mismatch;
- unexpected output enable, state transition, current or power;
- output/watchdog not confirmed disabled at a required gate;
- stale/unavailable measurement or required acquisition error;
- monitor/client action affects the hardware unexpectedly;
- mismatch between service runtime and independent physical observation;
- missing or changed workflow bytes/digest;
- limits/readback differ from approved values;
- controlled stop exceeds its approved bound;
- cleanup cannot verify output off, watchdog off, disabled channels and zero
  setpoints;
- orphaned process or uncertain IVI ownership;
- missing evidence needed to explain the result;
- operator requests a stop for any reason.

After a stop, preserve evidence first when safe, use the physical emergency
stop if state is uncertain, independently verify safe state, and classify the
test before deciding whether to repeat.

## 10. Per-test validation record template

```text
Campaign ID:
Test ID / title:
Date and local time:
Operator:
Assistant/session reference:

Repository branch:
Repository commit:
Working tree state:
32-bit Python version/architecture:
64-bit Python version/architecture:

NHR logical resource:
NHR serial:
DUT/fixture:
Profile path:
Workflow ID:
Bundle digest:
Controlled-stop policy:

Initial independent state:
Preconditions/gates confirmed:
Commands executed by assistant:
Physical actions performed by operator:

Observed console/API output:
Runtime/events observations:
CSV/report paths:
Independent final state:
Operator physical observations:

Acceptance criteria results:
Disposition: PASS / FAIL / BLOCKED / REPEAT
Deviation or anomaly:
Required correction:
Tests invalidated by correction:
Approval to continue:
```

## 11. Final campaign summary template

The final summary must contain:

1. exact branch, commit and software environment;
2. NHR identity and physical setup;
3. approved profiles, bundle digests, limits and stop policies;
4. test matrix with disposition and evidence links;
5. deviations, corrections and repeated tests;
6. measured cleanup/fallback behavior with its validation boundary;
7. independent final-state evidence;
8. operator confirmation of physical behavior;
9. unresolved risks and explicit exclusions;
10. recommendation: reject, continue development, or accept Milestones 1–4 for
    the reviewed physical configuration;
11. separate decision on readiness for Milestones 5–7 and v0.3.0 release.

## 12. New-thread handoff procedure

When validation begins in a new task:

1. open this document and `docs/SAFETY.md`;
2. inspect current branch/commit and configuration without modifying them;
3. begin at T0 and do not assume a prior test passed without its record;
4. state the current authorization tier and what remains unauthorized;
5. let the assistant execute routine software, simulator and evidence commands;
6. present one complete physical test card, obtain its gate, then execute the
   approved test block while the operator supervises the bench;
7. pause only at physical gates, unexpected results, disposition decisions or
   when operator evidence is required;
8. update the test record after each disposition;
9. stop before C0 until the battery envelope is supplied and approved;
10. request immediate operator confirmation before every energizing start;
11. keep hardware authorization and Git authorization as separate gates;
12. keep hands-on training exercises separate from campaign evidence.

## 13. Operator proficiency exercise track

### Purpose and role reversal

These exercises restore deliberate hands-on learning after the validation
campaign moves to assistant execution. During an exercise:

- the operator types commands, edits profiles, starts/stops processes and
  explains the observed behavior;
- the assistant provides the objective, safety boundary and acceptance criteria,
  then coaches rather than taking over;
- guidance decreases from worked examples to hints and finally review-only;
- simulator exercises precede physical exercises;
- no exercise grants hardware, energizing or Git authority to another exercise.

Exercise artifacts use a dedicated training output directory and are not added
to the approved physical workflow registry unless they pass a separate review.
Repository example profiles remain unapproved for physical activation.

### Curriculum

| Order | ID | Hands-on exercise | Operator capability demonstrated | Default environment |
|---:|---|---|---|---|
| 0 | L0 | Process and environment map | Select the correct 32/64-bit interpreter and explain service/client/monitor ownership | Local software |
| 1 | L1 | Simulator lifecycle from an empty console | Start, inventory, connect, observe, detach and stop without orphaned processes | Simulator |
| 2 | L2 | Configuration and profile authoring | Create, name, store and validate `rest`, CC, CP and CCCV profiles; identify unapproved values | Simulator/files |
| 3 | L3 | CSV dynamic profile | Build a signed current/power CSV, interpret zero and direction changes, and validate parsing failures | Simulator/files |
| 4 | L4 | Registry, digest and drift | Register an immutable workflow, compute its bundle digest, preflight it and diagnose byte drift | Simulator |
| 5 | L5 | Remote workflow lifecycle | Start with request/ack IDs, poll runtime/events, stop idempotently and recover after client loss | Simulator |
| 6 | L6 | Evidence and CSV interpretation | Locate acquisition/report artifacts, verify timestamps/rate/totals/termination and reconcile runtime with CSV | Simulator |
| 7 | L7 | Run with and without monitoring | Add/remove browser, monitor and second client; prove they do not own acquisition or workflow state | Simulator |
| 8 | L8 | Diagnostic fault lab | Diagnose wrong architecture, port conflict, stale process, bad resource, digest mismatch and stale measurement | Simulator/local |
| 9 | L9 | Physical read-only and `rest` operation | Perform identity, acquisition, independent observation and safe cleanup under Tier P/N gates | Physical, non-energizing |
| 10 | L10 | Approved low-energy workflow | Prepare, preflight, execute and independently close one C0-approved profile | Physical, Tier E |
| 11 | L11 | Operator capstone | Go from an empty console to a complete evidence package with assistant review only | Selected approved setup |

Charge and discharge are introduced as separate L10 variants. A combined
sequence is allowed only after both directions pass independently and the
approved fixture/module plan justifies the combination.

### Standard exercise format

Every exercise contains:

1. **Objective:** the capability the operator must demonstrate.
2. **Safety boundary:** simulator/physical tier and prohibited actions.
3. **Starting state:** processes, files, instrument and DUT state.
4. **Tasks:** one coherent scenario, not isolated copy/paste commands.
5. **Expected model:** ownership, state transitions and durable evidence.
6. **Injected fault:** at least one deliberate, safe diagnostic condition.
7. **Explain-back:** the operator explains the result and recovery in their own
   words.
8. **Acceptance:** observable criteria and artifacts required to pass.
9. **Reflection:** unclear steps, mistakes and improvements for the HOW_TO.

### Progression of assistance

- **Guided:** commands are explained and supplied, but executed by the operator.
- **Prompted:** the assistant supplies objectives and hints, not full commands.
- **Independent:** the operator plans and executes; the assistant reviews gates,
  evidence and conclusions.

An exercise advances only when the operator can explain why it worked, how to
detect failure and which process owned the instrument and evidence.

### HOW_TO deliverable after the exercises

After L0-L11, create a practical `docs/HOW_TO_OPERATE_NHR_RT.md` from the
validated exercise notes. It should contain:

- environment setup and 32/64-bit decision guide;
- service, client, CLI and monitor quick-starts;
- configuration/profile storage, naming, approval and digest workflow;
- simulator-first profile development;
- physical preflight, execution and cleanup gates;
- acquisition CSV, workflow report and event interpretation;
- with/without-monitor operating patterns;
- symptom-based troubleshooting and recovery;
- explicit boundaries between observation, non-energizing control and
  energizing authority.

The HOW_TO is written after the exercises so it captures tested operator
workflows and real misunderstandings rather than an idealized procedure.

## 14. Reference contracts

- [Repository overview](../../README.md)
- [Safety model](../../docs/SAFETY.md)
- [Service authority](../../docs/SERVICE_AUTHORITY.md)
- [Workflow registry and lifecycle](../../docs/WORKFLOWS.md)
- [Runtime observability](../../docs/RUNTIME_OBSERVABILITY.md)
- [Read-only monitor](../../docs/MONITOR.md)
- [Existing validation record](../../docs/VALIDATION.md)
- [Expansion roadmap](../../ROADMAP.md)
