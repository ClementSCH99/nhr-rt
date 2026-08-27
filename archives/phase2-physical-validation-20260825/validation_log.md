# Phase 2 physical validation log

> Archived chronological record. Intermediate statements describe campaign
> state at that point in time. Referenced raw files and approved profiles were
> removed during closeout; see [archive notes](README.md).

Campaign ID: `PHASE2-PHYSICAL-20260825-NHR79503`

Repository branch: `codex/milestone-4-readonly-web-monitor`

Repository commit: `6f335a995bea255062d05d8458074d58e5880ecb`

Initial working tree: modified `.gitignore` and `README.md`; untracked
`docs/PHASE2_PHYSICAL_VALIDATION_PLAN.md` and `service.local.json`.

## T0 - Environments and software baseline

- Disposition: `PASS` (previous validation confirmed by operator; baseline
  repeated on 2026-08-25).
- Python 3.12.10 32-bit service environment and Python 3.12.10 64-bit
  client/monitor environment verified.
- Regression: 121 passed, 2 skipped.
- Simulator acquisition and read-only monitor lifecycle verified.
- Browser and monitor loss did not alter simulator output state or acquisition.

## A1 - Physical connection and acquisition

- Authorization: Tier P A1-A6 block approved by operator on 2026-08-25 before
  physical service start.
- Configuration: `service.local.json`; instrument `nhr-79503`; logical resource
  `DC PM 1`; 10 Hz; operator supervision, primitive compatibility control and
  remote workflow control all disabled.
- Physical identity: serial 79503, independently confirmed in PowerPanel.
- Evidence CSV:
  `runs/nhr-79503_20260825T162250_234324Z.csv`.
- Acquisition: 315 samples; stable observed rate 10.0 Hz; no acquisition or IVI
  error.
- Software state: connected remotely; output disabled; state OFF; all channel
  enables false; all setpoints zero; no arm lease.
- Measured ranges while no DUT was connected: 33.0443 to 63.8690 V,
  0.0392 to 0.0674 A, 1.6211 to 4.0892 W, and 157.1876 to 157.2213 degC.
- Operator observation: output, watchdog, channels and setpoints independently
  confirmed disabled. The voltage is residual NHR voltage. The uncabled UUT
  temperature is false. Software measurements otherwise match PowerPanel.
- Interpretation: temperature is unavailable for validation and must not be
  treated as a valid DUT measurement or protection input. Residual open-terminal
  V/I/P values are retained as raw evidence and do not indicate intentional
  output enable.
- Safety response: service was stopped normally when the measurements first
  appeared unexplained. Final state was then independently verified in
  PowerPanel.
- Disposition: `PASS` with the documented non-DUT measurement limitations.
- Authorization status: expired after the out-of-sequence diagnostic service
  shutdown; a new bounded Tier P approval is required before A2.

## A2 - Independent observers

- Authorization: renewed Tier P A2-A6 block approved by operator with unchanged
  configuration, PowerPanel closed, NHR disabled and operator present.
- Evidence CSV:
  `runs/nhr-79503_20260825T162708_686811Z.csv`.
- Two independent 64-bit runtime polling clients and one read-only web monitor
  observed the same physical service simultaneously.
- With all observers attached, acquisition health remained `ok`, output remained
  disabled and sample count increased continuously.
- The second client was terminated first. The first client and monitor remained
  fresh, and the acquisition counter continued increasing.
- The first client was terminated next. The monitor remained live and the
  service continued acquisition.
- A newly attached client retrieved the same evidence path with fresh data and
  sample count 1399, versus 339 when the first observer attached.
- Expected diagnostic output: terminating each polling helper with Ctrl+C
  produced a local `KeyboardInterrupt`; it did not affect the service.
- Software disposition: `PASS`.
- Operator physical observation: no transition observed during observer
  attach/detach.
- Disposition: `PASS`.

## A3 - Physical primitive-control rejection

- Special request gate: operator approved exactly one primitive request with
  body `{"name": "disable"}`.
- Before the request, both `service.local.json` and
  `GET /api/v1/configuration` reported
  `primitive_compatibility_control: false`.
- Request: `POST /api/v1/instruments/nhr-79503/command` with body
  `{"name": "disable"}`.
- Response: HTTP 403, `NHRPolicyError`, with message that primitive
  compatibility control is disabled for the physical instrument.
- Before/after state: connected, OFF, output disabled, all channel enables
  false, all setpoints zero, workflow idle, acquisition healthy on the same
  evidence path.
- Procedural observation: the first shell attempt failed during PowerShell
  parsing before Python or HTTP execution. The approved request was then sent
  exactly once using `tier_p_a3.py`.
- Software disposition: `PASS`.
- Operator physical observation: no transition observed.
- Disposition: `PASS`.

## A4 - Runtime and independent-state consistency

- Snapshot time: 2026-08-25 12:33 local / 16:33 UTC.
- Runtime: connected, remote, state OFF, output disabled; all channel enables
  false; all setpoints zero; workflow idle.
- Runtime measurement: 62.8375 V, 0.0482 A, 3.0071 W; fresh with age 0.171 s.
- Closest CSV row: 62.8774 V, 0.0490 A, 3.0440 W; state OFF; setpoints zero;
  interlocks column `ok`.
- Monitor shortly afterward: OFF, output disabled, fresh; 62.89 V, 0.05 A,
  3.0 W; acquisition health OK at 10.00 Hz.
- Acquisition snapshot: 3433 samples, 10.00035 Hz, same evidence path, no
  error. Monitor displayed 3579 samples shortly afterward.
- Workflow progress: unavailable while idle, as required.
- External sources and effective power limits: not configured, as expected for
  Milestones 1-4.
- Alert: `operator_supervision` is intentionally unsafe because physical
  workflow authority remains disabled in `service.local.json`.
- UUT temperature remains invalid because the input is not wired and is
  excluded from physical consistency acceptance.
- Software consistency: `PASS`.
- Independent PowerPanel comparison: operator reported all values similar and
  no physical transition.
- Disposition: `PASS`.

## A5 - Monitor loss isolation

- Initial client snapshot: acquisition sample count 4404, health `ok`, fresh
  measurement, output disabled, same evidence path as A2-A4.
- Monitor page opened successfully and displayed OFF, output disabled, fresh
  measurements and acquisition sample count 4533 at 10.00 Hz.
- Browser tab closed: client subsequently reported sample count 4670, health
  `ok`, fresh measurement and output disabled.
- Browser page reopened: it recovered current live state without an IVI reset;
  monitor sample count 4728.
- Monitor process stopped normally with Ctrl+C: service remained available and
  acquisition advanced to sample count 4870 on the same evidence path.
- Monitor process restarted: the existing page recovered current runtime state,
  reporting OFF, output disabled, fresh measurement and sample count 5109.
- Trends remained explicitly local/browser-buffered while durable acquisition
  continued uninterrupted.
- Software disposition: `PASS`.
- Operator physical observation: no transition or anomaly observed during
  browser and monitor loss/recovery.
- Disposition: `PASS`.

## A6 - Graceful service shutdown with output disabled

- Pre-shutdown runtime at 2026-08-25 16:36:49 UTC: workflow idle, output
  disabled, all channel enables false, all setpoints zero, acquisition healthy
  at 10.0002 Hz with sample count 5608.
- Observation: runtime `remote` changed from true earlier in the block to false
  after the PowerPanel comparison. Output state and acquisition were unchanged.
  This is provisionally attributed to PowerPanel returning the instrument to
  local mode and requires operator confirmation.
- Service received normal Ctrl+C at 12:37:01 local.
- Cleanup log: service shutdown requested, service closing, service stopped at
  12:37:02 local. Elapsed console time was approximately 0.88 s.
- No active workflow existed during shutdown.
- Independent state after first shutdown: operator reported no anomaly and
  approved continuation.
- PowerPanel was confirmed closed before service restart.
- Restart succeeded with a fresh IVI connection, remote true, output disabled,
  all channel enables false, all setpoints zero and no error.
- Fresh acquisition used
  `runs/nhr-79503_20260825T163802_067868Z.csv`; the five-second checkpoint
  reported 50 samples at 10.0513 Hz with a fresh measurement.
- After restart verification, monitor and service were stopped normally because
  no further test was authorized in the Tier P block.
- Final restart-lifecycle CSV contains 216 samples from 16:38:26.981514 to
  16:38:48.490129 UTC; every row reports state OFF, current/power setpoints zero
  and no acquisition error.
- No remaining nhr-rt Python/NHR process and no listener on ports 9300/9400 was
  found after cleanup.
- Monitor-only observation: while the browser continued polling during service
  unavailability, the monitor correctly returned HTTP 502/offline responses.
  One in-flight browser disconnect produced a local request-handler traceback
  (`ConnectionAbortedError`); it did not affect IVI cleanup or physical state.
- Software disposition: `PASS`.
- Final independent physical state after the last cleanup: operator confirmed
  output OFF, watchdog OFF, all channels disabled and all setpoints zero.
- Final disposition: `PASS`.

## Tier P A1-A6 block disposition

| Test | Disposition | Principal evidence/observation |
|---|---|---|
| A1 | PASS | Identity 79503, stable 10 Hz acquisition, independent safe-state confirmation |
| A2 | PASS | Independent observer detach/reattach did not interrupt acquisition or change physical state |
| A3 | PASS | Primitive `disable` request rejected HTTP 403 before IVI; no state change |
| A4 | PASS | Runtime, CSV, monitor and PowerPanel mutually consistent |
| A5 | PASS | Browser/monitor loss and recovery isolated from service acquisition and hardware state |
| A6 | PASS | Normal service cleanup, successful fresh restart lifecycle and independently verified final safe state |

Tier P A1-A6 is accepted for the reviewed no-DUT configuration. Tier N, Tier E
and Z1 were not executed and are not implied by this result.

## Tier N shared contract and artifact preparation

- Operator approval received on 2026-08-25 for the no-DUT Tier N contract:
  physical `rest` duration 30 s, watchdog enabled during the run,
  controlled-stop timeout 3.0 s, logical resource `DC PM 1`, serial `79503`
  and accessible physical emergency stop.
- Approved static limits: charge 5 A / 100 V maximum / 500 W; discharge 5 A /
  80 V minimum / 500 W.
- Approved profile and workflow ID: `phase2-tier-n-rest-v1`.
- Prepared profile:
  `approved/phase2-tier-n-rest-v1.json`.
- Exact immutable bundle digest:
  `sha256:0ed18633cd0a259f059155fd592e3c41a1b55bea1a161468b494c583fda00e2e`.
- Offline hardware-schema and registry validation: available, one stage, no
  profile error. This did not connect to IVI or start the physical service.
- `service.local.json` prepared with operator supervision and only the approved
  remote-workflow path enabled; primitive compatibility remains disabled.
- B1-B5 remain unexecuted. The per-test physical gates remain required.

## B1 - Registry, digest and physical preflight

- Authorization: B1 explicitly approved by the operator with output OFF,
  watchdog OFF, channels disabled, setpoints zero, PowerPanel closed and the
  physical emergency stop accessible.
- Registry exposed exactly one available workflow with digest
  `sha256:0ed18633cd0a259f059155fd592e3c41a1b55bea1a161468b494c583fda00e2e`.
- Unknown workflow ID and an incorrect digest were both rejected before
  physical preflight.
- The approved preflight completed service-side in approximately 10.64 s and
  wrote
  `runs/workflow-preflights/preflight-20260825T185849-2dd1f5b0/report.json`.
- Durable report: `passed: true`; identity `DC PM 1` / `79503`; initial output
  disabled; initial watchdog disabled; requested static safety limits matched
  readback with no mismatch; no emergency fallback; cleanup and independent
  reconnect reported output disabled, watchdog disabled, channels disabled and
  zero setpoints; `final_safe_state_verified: true`.
- Acquisition recovered after reconnect at approximately 10 Hz with fresh
  measurements and no acquisition error. The uncabled temperature and residual
  open-terminal V/I/P retain the Tier P interpretation and are not DUT evidence.
- Contradiction: the public 64-bit client timed out after 10.0 s before the
  successful physical preflight response was returned. The service then logged
  the resulting aborted HTTP response. This makes the approved preflight appear
  failed to the initiating client despite durable service-side success.
- The optional disk-drift check was not executed after this contradiction.
- Service cleanup: normal Ctrl+C shutdown completed in approximately 0.46 s;
  no listener remained on ports 9300 or 9400.
- Software disposition: `FAIL` because the valid public preflight request did
  not return successfully to the client. B2-B5 are blocked pending correction,
  regression and repeat of B1.
- Operator final physical observation: output OFF, watchdog OFF, channels
  disabled, setpoints zero and no anomaly observed.

### B1 corrective software validation

- Root cause: `NHRServiceClient` applied a fixed 10.0 s timeout to every HTTP
  request, while the physical preflight completed synchronously in 10.64 s.
- Authorized correction: `preflight_workflow()` now uses a configurable
  `timeout_s` with a 30.0 s default. Ordinary client and monitor requests retain
  their 10.0 s timeout. No service, workflow, limit or hardware-control logic
  changed.
- Focused workflow-service regression: `12 passed in 9.35s`.
- Full software regression: `122 passed, 2 skipped in 55.92s`; the skips are
  the explicitly gated physical tests and no hardware opt-in was supplied.
- An earlier focused run under a new OneDrive-local basetemp ended in the known
  pytest `WinError 5` temporary-directory failure and was discarded as
  infrastructure evidence before the clean rerun.
- Offline post-change verification confirmed the physical bundle remains
  unchanged at
  `sha256:0ed18633cd0a259f059155fd592e3c41a1b55bea1a161468b494c583fda00e2e`
  and the registry still exposes exactly one available `rest` workflow.
- Per the campaign invalidation rule, B1 remains `FAIL` until a fresh physical
  repeat passes. B2-B5 remain blocked.

### B1 complete repeat after timeout correction

- Fresh B1 authorization received with the required independent initial safe
  state and emergency-stop readiness.
- Registry again exposed exactly one available workflow and the approved
  digest. Unknown workflow ID and incorrect digest requests were rejected.
- The approved public-client preflight returned successfully within the new
  30 s bound with `passed: true`; preflight ID
  `preflight-20260825T190538-9681a9aa`.
- Durable report:
  `runs/workflow-preflights/preflight-20260825T190538-9681a9aa/report.json`.
- Identity was `DC PM 1` / `79503`; initial output and watchdog were disabled;
  all six requested safety limits matched IVI readback; cleanup and reconnect
  verified the complete safe state; no emergency fallback was used.
- After reconnect, acquisition reached 10.005 Hz with 500 samples, fresh
  measurements and no error. Workflow state remained idle and no workflow was
  started.
- Controlled disk-drift probe: a temporary text-only change to the registered
  profile was rejected with `Workflow bundle changed after service startup;
  restart required`. The exact approved bytes were restored and the offline
  digest reverified as
  `sha256:0ed18633cd0a259f059155fd592e3c41a1b55bea1a161468b494c583fda00e2e`.
- Immediately before shutdown, runtime reported output disabled, all channel
  enables false, all setpoints zero, acquisition healthy and workflow idle.
- Normal service shutdown completed in approximately 0.83 s; no listener
  remained on ports 9300 or 9400.
- Software acceptance criteria: all passed.
- Operator final physical observation: output OFF, watchdog OFF, channels
  disabled, setpoints zero and no anomaly observed.
- Final disposition: `PASS`.
- B2 remains blocked until a fresh B2 gate is granted.

## B2 - Service-owned physical rest workflow

- Authorization: B2 explicitly approved for the immutable 30 s physical
  `rest` workflow, no DUT, no output enable, operator present and physical
  emergency stop accessible.
- Fresh preflight `preflight-20260825T191500-67feb4ab` passed with the exact
  approved identity, digest, limits/readback and initial/final safe states.
- Run ID: `b2b32284-f781-44bf-ae3d-b734db7c8966`; request ID:
  `d9047c31-0cf2-49f0-b886-fba563e6fb27`.
- The service accepted the run and reached `preflighting`, then `running` on the
  single `tier-n-rest` stage. Output, channel enables and setpoints were zero in
  the last available runtime evidence; Ah/Wh totals remained zero.
- The read-only monitor request timed out while the workflow held the service
  lifecycle lock. The initiating observation helper exited without requesting
  workflow stop. A second client recovered the same service-owned run ID.
- The run was still `running` at 55.328 s for a reviewed 30.0 s target, with
  progress current greater than target and no terminal report. The test was
  stopped under the campaign stop criteria.
- Run-specific cooperative stop was accepted, but the run was still
  `stop_requested` after more than 3.0 s. The controller recorded
  `emergency_fallback_requested: true` and still did not reach a terminal state
  or verified safe state.
- Durable acquisition
  `runs/nhr-79503_20260825T191525_811125Z.csv` contains only 10 samples. The
  final sample is at 19:15:35.389747 UTC, approximately 9.5 s after run start;
  no subsequent sample or acquisition error was written. This aligns with the
  first monitor/runtime timeout and indicates the IVI worker stopped making
  progress during a measurement operation.
- Normal service shutdown and a second Ctrl+C did not complete. The operator
  engaged the physical emergency stop and independently confirmed output OFF.
  The NHR watchdog remained active and the unit entered a latched state pending
  local reset.
- After independent physical confirmation, only the verified stuck service
  processes were force-terminated: PID 23352 (32-bit owner/listener) and PID
  24368 (`.venv32` launcher). The monitor was also stopped. No listener remained
  on ports 9300 or 9400.
- The durable run manifest remains `stop_requested`, emergency fallback
  requested, terminal outcome absent and `final_safe_state.verified: false`.
  No workflow report was produced.
- Preliminary software diagnosis: backend calls have no bounded wait at the
  `NHR9300._call()` boundary, so a blocked IVI-COM measurement occupies the
  sole worker and prevents queued stop/watchdog-disable/close operations. In
  addition, runtime snapshots wait on the workflow-held lifecycle lock, making
  read-only observability unavailable during the active physical workflow.
- Operator-reported local fault context after the stop: watchdog active/service
  timeout; out-of-limit because voltage was too low; system OFF with reset
  required to clear the fault. With no DUT connected, the previously observed
  open-terminal voltage of approximately 33-64 V is below the approved
  `discharge_voltage_min` of 80 V. Enabling the watchdog therefore made this
  no-DUT bench/profile combination predictably fault-provoking. This is the
  primary explanation for the latched NHR state and subsequent IVI loss of
  progress; the unbounded worker wait and runtime lock remain software
  resilience/observability findings, but this run does not isolate them as an
  independent root cause.
- Final disposition: `FAIL`.
- B3-B5 are `BLOCKED`. No automatic NHR reset or further physical/software test
  is authorized.

## Tier N no-DUT v2 preparation

- Operator approved a dedicated no-DUT Tier N profile with
  `discharge_voltage_min: 0.0 V` after the v1 profile predictably faulted on
  open-terminal voltage below 80 V.
- New immutable workflow/profile ID:
  `phase2-tier-n-no-dut-rest-v2`.
- Profile path:
  `approved/phase2-tier-n-no-dut-rest-v2.json`.
- Exact bundle digest:
  `sha256:74cda714d0a795405349cf91284a2ff720ef4e18e956f394018de3ab1c45a8b3`.
- The registry contains only v2 and reports it available with one 30 s `rest`
  stage. The v1 file remains preserved as historical evidence but is not
  registered.
- Offline hardware-schema validation passed. This does not establish that the
  physical instrument accepts a 0 V discharge-limit readback; that is a B1
  preflight criterion.
- No service or IVI access occurred during v2 preparation.

### B1 repeat with no-DUT v2 bundle

- B1 v2 was explicitly authorized after independent confirmation of output
  OFF, watchdog OFF, disabled channels, zero setpoints, cleared fault,
  PowerPanel closed and emergency-stop readiness.
- Registry exposed exactly one available v2 workflow. Unknown workflow ID and
  incorrect digest requests were rejected.
- Physical preflight `preflight-20260825T192751-b82d9a69` returned
  `passed: true` with identity `DC PM 1` / `79503` and no safety-limit mismatch.
- The NHR accepted and read back `discharge_voltage_min: 0.0 V` exactly. The
  other approved 5 A / 100 V / 500 W charge and 5 A / 500 W discharge limits
  also matched readback.
- Cleanup/reconnect verified output disabled, watchdog disabled, all channels
  disabled and zero setpoints. Acquisition recovered at approximately 10 Hz
  with fresh measurements and no error; workflow remained idle.
- Controlled v2 disk drift was rejected before execution. The approved bytes
  were restored and the digest reverified as
  `sha256:74cda714d0a795405349cf91284a2ff720ef4e18e956f394018de3ab1c45a8b3`.
- Normal service shutdown completed in approximately 0.65 s and no listener
  remained on ports 9300 or 9400.
- Software acceptance criteria: all passed.
- Operator final physical observation: output OFF, watchdog OFF, channels
  disabled, setpoints zero, no fault and no anomaly observed.
- Final disposition: `PASS`.

### Runtime/monitor correction before B2 v2

- Authorized scope: remove only the workflow lifecycle lock from consolidated
  runtime snapshot assembly, retain cached/read-only data sources and their
  internal locks, strengthen the B2 observer helper, and run software tests.
- `runtime_snapshot()` no longer waits for the hardware workflow lifecycle
  lock. It still uses cached instrument status, collector state, cached safety
  state and the workflow controller snapshot; it performs no new IVI read.
- New regression holds the lifecycle lock in another thread and proves the
  public runtime request completes within 0.5 s.
- The B2 helper now targets the exact no-DUT v2 workflow and records a monitor
  error without abandoning run-state supervision.
- Focused runtime/workflow regression: `21 passed in 13.69s`.
- Full-suite qualification chronology:
  - first run: `1 failed, 122 passed, 2 skipped`; the known intermittent 64-bit
    subprocess workflow test returned `failed`, then passed in isolation;
  - second run: `1 failed, 122 passed, 2 skipped`; a Windows monitor HTTP test
    raised `ConnectionAbortedError`, then passed in isolation;
  - third diagnostic run again exposed the intermittent 64-bit subprocess
    outcome, so the test assertion was enhanced to retain error/report context;
  - final full run: `123 passed, 2 skipped in 46.68s`.
- The two skips remain the explicitly gated physical tests; no hardware opt-in,
  service start or IVI access occurred during this correction and regression.
- B1 v2 remains valid because registry, preflight, bundle, limits and hardware
  control paths were unchanged. B2 requires a fresh named physical gate.

## B2 repeat - no-DUT v2 rest workflow

- Authorization: B2 v2 explicitly approved with output OFF, watchdog OFF,
  disabled channels, zero setpoints, no fault, PowerPanel closed, operator
  present and physical emergency stop accessible.
- Fresh preflight `preflight-20260825T193926-0b3391c9` passed with the exact v2
  digest, identity and 0 V discharge-minimum readback.
- Run ID: `2dc20df4-b7c5-4bc5-9dba-a1e1843a67ed`; request ID:
  `e9b22d48-4288-48d7-a232-2599c3f3427b`.
- Client, consolidated runtime and monitor all observed accepted/preflighting
  and running states without blocking. At the sampled running transition,
  output was disabled, all channels disabled, all numeric setpoints zero,
  acquisition fresh and the exact run identity matched across consumers.
- The single `tier-n-no-dut-rest` stage passed by configured duration. Routine
  elapsed time was 30.20 s; 312 global samples over 31.188 s at 9.997 Hz; 304
  stage samples; no acquisition error; directional Ah/Wh totals all zero.
- Durable report:
  `runs/workflow-runs/2dc20df4-b7c5-4bc5-9dba-a1e1843a67ed/report.json`.
  It records `passed: true`, no error, no cleanup error, no emergency fallback,
  and independently verified cleanup/reconnect safe states.
- Procedural anomaly: near terminal cleanup, the helper detected an allegedly
  active output/channel or non-zero setpoint and requested run-specific stop,
  but did not emit the triggering snapshot before raising. The stop request
  raced with successful cleanup: the final state remained `passed`, with
  `stop_requested: true`, no emergency fallback and verified safe state.
- CSV evidence contains 303 rows in OFF and one row in STANDBY; no recorded
  voltage/current/power setpoint is non-zero. Initial, cleanup, reconnect and
  post-run runtime snapshots all show output disabled, all channels disabled
  and every setpoint zero. The triggering transient therefore cannot be
  reconstructed from durable evidence.
- Monitor and service stopped normally; no listener remained on ports 9300 or
  9400.
- Operator final physical observation: output OFF, watchdog OFF, channels
  disabled, setpoints zero and no fault; no abnormal transition was observed
  during or at the end of the run.
- Provisional disposition: `REPEAT` because the observer helper created an
  unexplained stop request and terminal monitor evidence is incomplete, even
  though the service-owned workflow report itself passed.
- B3-B5 remain blocked.

### B2 observer-helper correction

- The helper now refreshes the run state before evaluating runtime, preventing
  a stale non-terminal state from extending observation into an already
  terminal run.
- Any future output/channel/setpoint violation is emitted with the complete
  triggering runtime snapshot before a stop request is sent.
- The revised helper compiles under both the 32-bit and 64-bit project
  environments. No product code, bundle, limit, service process or IVI state
  changed.
- A fresh B2 physical gate is required for the repeat.

### B2 final repeat - no-DUT v2 rest workflow

- Authorization: the B2 v2 repeat was explicitly approved after confirmation
  of output OFF, watchdog OFF, disabled channels, zero setpoints, no fault,
  PowerPanel closed, operator presence and physical emergency-stop access.
- Fresh preflight `preflight-20260825T194356-5ceb1be0` passed with the exact
  v2 bundle digest, identity and approved safety-limit readback.
- Run ID: `f2fc1761-b3b7-49d3-b184-427987eea66a`; request ID:
  `221d54c3-f1cd-4d04-be4e-d812cd9d7111`.
- The service-owned workflow passed without a stop request or emergency
  fallback. The 30 s rest stage elapsed in 30.210 s. Global acquisition
  recorded 311 samples over 31.094 s at 9.995 Hz with no acquisition error;
  the stage CSV contains 305 samples spanning 30.134 s.
- All 305 durable CSV rows have zero voltage/current/power setpoints, zero
  directional capacity and energy, `interlocks: ok`, no error, and only
  `standby` or `off` states.
- Initial, cleanup, reconnect, final consolidated-runtime and monitor snapshots
  agree: output disabled, watchdog disabled after cleanup, all channels
  disabled, all numeric setpoints zero, acquisition healthy and workflow idle.
- Durable report:
  `runs/workflow-runs/f2fc1761-b3b7-49d3-b184-427987eea66a/report.json`.
  It records `passed: true`, no safety-limit mismatch, no error, no emergency
  fallback and `final_safe_state_verified: true`.
- The corrected observer helper exited normally and recorded no unsafe-runtime
  snapshot, no monitor timeout and no stop request.
- Monitor and service stopped normally; no listener remained on ports 9300 or
  9400.
- Software-evidence disposition: `PASS`.
- Operator final physical observation: output OFF, watchdog OFF, channels
  disabled, setpoints zero, no fault and no abnormal physical transition.
- Final disposition: `PASS`.
- B3-B5 may proceed only after their explicit named Tier N authorization.

## B3 - Controlled stop during no-DUT rest

- B3, B4 and B5 were explicitly authorized as named Tier N tests with the
  unchanged `phase2-tier-n-no-dut-rest-v2` bundle, no DUT and no output enable.
- A first helper attempt ended before workflow start because the fresh service
  had no cached instrument status. A second start request was rejected before
  workflow creation because recovery required a successful preflight.
- Required physical preflight `preflight-20260825T195022-60f64c69` passed.
- Run ID: `2952b3d6-7d92-4bb5-89b2-66469684e84b`; request ID:
  `8c6d0fcb-b4c6-4db8-8ea7-11e4377571ac`.
- The run reached `running` with output disabled, all channels disabled and all
  setpoints zero. The first run-specific stop returned HTTP 202 in 0.015 s;
  the repeated request returned HTTP 200 without another action or error.
- The routine itself stopped normally, but complete workflow cleanup reached
  terminal `stopped` 3.390 s after the first request. This exceeded the
  approved 3.0 s controlled-stop bound, so
  `emergency_fallback_requested: true` and the instrument last-error reported
  `Controlled workflow stop exceeded its approved timeout`.
- The durable report records stopped outcome, zero Ah/Wh, output/watchdog OFF,
  disabled channels, zero setpoints, successful reconnect and
  `final_safe_state_verified: true`. Its `emergency_fallback_used` field is
  false, but the run-level fallback request and instrument error violate the
  B3 criterion that cooperative stop complete without fallback.
- Monitor and service were stopped normally; neither port 9300 nor 9400 remains
  open.
- Software disposition: `FAIL` pending operator physical-state confirmation.
- B4-B5 are blocked pending B3 disposition and confirmation of bench state.

- Operator physical confirmation after B3: output OFF, watchdog OFF, channels
  disabled, setpoints zero; NHR displayed `OFF` and `READY`.
- B3 final disposition: `FAIL`. The bench was confirmed safe, so the separately
  authorized B4 could proceed without changing the bundle or configuration.

## B4 - Initiating-client loss during no-DUT rest

- The service required recovery preflight after restart. Preflight
  `preflight-20260825T195506-ca3f0bf7` passed before workflow creation.
- Run ID: `368503e1-5927-4b69-b4a9-2edb19816700`; request ID:
  `76793f35-dcca-482c-b3c0-43ac825b622e`.
- The separate 64-bit initiating client exited normally immediately after the
  start response. A distinct observer retained and followed the same run ID
  through preflighting, running and passed states; a fresh client then
  retrieved the original run, and the monitor agreed.
- The workflow passed normally with no stop request or fallback. Acquisition
  recorded 311 samples at 9.575 Hz, zero Ah/Wh, no output enable, and final
  cleanup/reconnect verified output/watchdog OFF, channels disabled and
  setpoints zero.
- Software-evidence disposition: `PASS`.
- Operator final physical observation: output OFF, watchdog OFF, channels
  disabled, setpoints zero, no fault or anomaly.
- B1 v3 final disposition: `PASS`.
- B3 v3 final disposition: `PASS`.

## Tier N corrected final status

- Active validated contract: `phase2-tier-n-no-dut-rest-v3`, controlled-stop
  bound 5.0 s, digest
  `sha256:5e14ae28b0c4c21c3c87dc082e90cab88f526211866fd0a1a2ab6121623099ea`.
- B1 v3 registry/digest/physical preflight: `PASS`.
- B2 service-owned no-DUT rest: `PASS` (v2 evidence remains applicable because
  stage and electrical limits are unchanged in v3).
- B3 controlled stop: `PASS` on v3 at 3.766 s without fallback.
- B4 initiating-client loss: `PASS` (v2 evidence remains applicable because
  client-loss ownership behavior, stage and electrical limits are unchanged).
- B5 service shutdown/recovery under v2: `PASS`. Because B5 exercises the
  controlled-stop policy that changed in v3, B5 v3 is explicitly `NOT REPEATED`
  and is not represented as v3 physical evidence.
- Historical v2 B3 remains recorded as `FAIL` at 3.390 s versus its former
  3.0 s bound; it is not rewritten or represented as a pass.
- Final bench observation: output/watchdog OFF, channels disabled, setpoints
  zero, no fault or anomaly. Service and monitor are stopped.

### B5 v3 physical repeat and Tier N closeout

- Operator authorized the B5 repeat under the active v3 contract and remained
  available for the mandatory physical gate before restart.
- Initial/recovery preflight `preflight-20260825T201107-5c102f87` passed for the
  exact v3 digest and identity `DC PM 1` / `79503`.
- Run ID: `d1dd78fd-d334-41ff-acb2-31ae4a19c5b8`; request ID:
  `8e2e2cb4-c24a-4c0c-a14a-a716d02d3a44`.
- The run reached the reviewed 30 s rest stage with output OFF, channels
  disabled and setpoints zero. Normal service shutdown was requested at
  `20:11:15.445Z`.
- The workflow persisted terminal `stopped` at `20:11:18.855Z`, approximately
  3.410 s after the stop request and below the v3 5.0 s bound. Service shutdown
  completed in approximately 3.884 s.
- No emergency fallback was requested or used. The report records zero Ah/Wh,
  no error, output/watchdog OFF, successful reconnect and verified final safe
  state.
- Operator independently confirmed before restart: output OFF, watchdog OFF,
  channels disabled, setpoints zero, no fault or anomaly.
- After restart, the same run was explicitly recovered as terminal `stopped`
  with verified safe state and no fallback. Final recovery preflight
  `preflight-20260825T201231-fa7bcbf1` passed, and runtime remained idle/safe.
- Service then stopped normally. No process listens on ports 9300 or 9400.
- B5 v3 final disposition: `PASS`.
- Tier N active-contract disposition: `PASS`. B2 and B4 evidence is carried
  forward because v3 changed only the controlled-stop bound/identity; their
  executed lifecycle paths, rest stage and electrical limits are unchanged.
- Cleanup removed the failed `.test-temp-b3-v3` directory and the campaign
  Python cache. `.test-temp-final-2` remains because Windows/OneDrive denied
  deletion; it is an existing qualified pytest basetemp, not campaign evidence.
- Tier E remains unauthorized and requires its separate C0 contract and
  immediate per-run gates.
- Service and monitor remain active in a safe idle state to avoid a redundant
  restart before B5.

- Operator final B4 observation: output OFF, watchdog OFF, channels disabled,
  setpoints zero, NHR `OFF/READY`, no anomaly.
- B4 final disposition: `PASS`.

## B5 - Normal service shutdown during no-DUT rest

- Run ID: `5bf74ad8-b2dd-4ea6-b68f-831c20adfc73`; request ID:
  `9029f3f0-aa8e-4f33-9f7c-2fb214d68101`.
- Before shutdown, the run was active in the reviewed rest stage with output
  disabled, all channels disabled and setpoints zero.
- Normal console shutdown was requested at `19:58:14.316Z`. The service first
  requested the workflow stop, closed its resources and exited at
  `19:58:19.459Z`, approximately 5.143 s later.
- The run manifest explicitly records terminal `stopped`, `stop_requested:
  true`, zero Ah/Wh, verified final safe state and successful reconnect. It also
  records `emergency_fallback_requested: true` because the complete workflow
  cleanup again exceeded the approved 3.0 s controlled-stop timeout.
- The durable report records output/watchdog OFF, disabled channels, zero
  setpoints, no workflow error and no emergency fallback reported as used; the
  post-reconnect instrument error retains the timeout message.
- The monitor transitioned to an explicit HTTP 502/offline state after service
  loss and was then stopped normally.
- No service restart has occurred. B5 is paused at the mandatory independent
  physical-state gate before recovery inspection and preflight.

- Operator confirmation before restart: output OFF, watchdog OFF, channels
  disabled, setpoints zero, no fault or anomaly.
- After restart, the B5 manifest was recovered explicitly as terminal
  `stopped` with verified safe state; it was not discarded or silently changed.
- Recovery inspection found the previously retained interrupted manifest
  `b2b32284-f781-44bf-ae3d-b734db7c8966`, which sets the service recovery block
  before preflight. Earlier attempted starts in this campaign demonstrated the
  corresponding HTTP 403 rejection; no new start was attempted during B5
  recovery.
- Recovery preflight `preflight-20260825T200059-db2f9ad3` passed with identity
  `DC PM 1` / `79503`, no safety-limit mismatch and verified final safe state.
  Runtime afterward was idle with output OFF, watchdog OFF after cleanup,
  channels disabled and setpoints zero.
- The recovered service was stopped normally after the preflight. Ports 9300
  and 9400 have no listening process (only transient TCP `TIME_WAIT` entries).
- Final disposition: `PASS`, with the 3.0 s controlled-stop overrun retained as
  the B3 failure and as a B5 shutdown observation.

## Tier N batch summary

- B1 registry/digest/physical preflight: `PASS`.
- B2 service-owned no-DUT rest: `PASS`.
- B3 controlled stop during rest: `FAIL` (3.390 s versus approved 3.0 s;
  emergency fallback requested).
- B4 initiating-client loss during bounded rest: `PASS`.
- B5 normal service shutdown/recovery/preflight: `PASS` with the known B3
  timeout behavior observed during shutdown.
- Final process state: service and monitor stopped; no listener on 9300/9400.

## B3 timeout analysis and v3 correction

- Root cause: the approved controlled-stop timer joins the complete service-owned
  workflow thread, so it includes routine stop plus watchdog cleanup, safe-state
  verification, IVI close and reconnect. It does not measure only the routine's
  response to the stop event.
- In B3, the routine stopped promptly but complete terminal persistence took
  3.390 s. During B5, terminal persistence took approximately 4.386 s after the
  shutdown stop request. Both exceeded the v2 3.0 s policy even though cleanup
  ultimately verified a safe state.
- Operator requested correction based on a 5.0 s controlled-stop bound. A new
  immutable bundle was created; v2 remains unchanged as historical evidence.
- New workflow/profile ID: `phase2-tier-n-no-dut-rest-v3`.
- New profile path: `approved/phase2-tier-n-no-dut-rest-v3.json`.
- New exact bundle digest:
  `sha256:5e14ae28b0c4c21c3c87dc082e90cab88f526211866fd0a1a2ab6121623099ea`.
- The rest stage, no-DUT configuration and all electrical safety/workflow limits
  are unchanged. Only the stop procedure, policy timeout and v3 identity changed.
- `service.local.json` now registers only v3 and configures an approved 5.0 s
  controlled-stop policy named `phase2-tier-n-no-dut-rest-v3`.
- The observed worst case leaves approximately 0.614 s margin below 5.0 s; this
  is sufficient for the recorded campaign behavior but remains subject to a
  physical B3 repeat.
- Offline hardware-schema bundle loading and registry/config/digest consistency
  checks passed. The v3 B3 helper compiles in both project environments.
- Focused workflow-service regression: `12 passed in 8.62s`.
- An earlier pytest attempt using a new OneDrive-local temporary directory hit
  the known Windows `PermissionError: [WinError 5]`; the qualified project
  basetemp produced the clean result above.
- No service start, IVI access or physical command occurred during correction.
- Required next gate: exact v3 B1 physical preflight followed by a named B3 v3
  repeat; no workflow is authorized by this software preparation alone.

### B1 v3 preflight and B3 v3 physical repeat

- Operator explicitly authorized B1 v3 followed by B3 v3 with output/watchdog
  OFF, channels disabled, setpoints zero, no fault and emergency stop accessible.
- Physical preflight `preflight-20260825T200701-be43d48e` passed for exact v3
  digest and identity `DC PM 1` / `79503`, with no safety-limit mismatch and a
  verified final safe state.
- Run ID: `14cebce0-d591-4ce8-8898-e433d32061bf`; request ID:
  `4406d35c-6865-454f-9719-e1809876a747`.
- The run reached `running` with output OFF, all channels disabled and setpoints
  zero. The first stop returned HTTP 202; the repeated request returned HTTP 200
  without a duplicate action or error.
- Terminal `stopped` was observed 3.766 s after the first stop request, below
  the approved 5.0 s v3 bound. No emergency fallback was requested or used.
- The durable report records routine stop requested, no stage error, zero Ah/Wh,
  output/watchdog OFF after cleanup, verified final safe state and successful
  reconnect.
- Service stopped normally afterward; no listener remains on ports 9300/9400.
- B1 v3 software evidence: `PASS`.
- B3 v3 software evidence: `PASS`.
- Final physical disposition: `PENDING OPERATOR CONFIRMATION`.

## C0 - Battery module and Tier E profile approval gate

- Date: 2026-08-26 local.
- Operator-approved DUT: NMC module, 24s2p, 66 Ah, approximately 55% SOC.
- Initial independent observation: 88.7 V and 25 degC; output OFF, watchdog
  OFF, channels disabled, setpoints zero, polarity verified and no anomaly.
- Approved test voltage window: 80.0 to 100.0 V.
- Approved static safety limits: 10.0 A charge/discharge and 1000 W
  charge/discharge.
- Approved C1 workflow: CCCV charge at 5.0 A to 89.5 V, 4.5 A cutoff,
  500 W workflow limit, maximum 60 s active stage and final 10 s `rest`.
- Approved controlled-stop bound: 5.0 s. Approved C6 watchdog observation
  window: 10.0 s.
- Temperature: the unwired NHR UUT-temperature value is ignored. The operator
  continuously monitors an independent external measurement and manually stops
  at 50 degC or on any unexpected heating.
- BMS is monitoring-only and has no contactor. There is no fuse in the reviewed
  setup. The operator explicitly accepted this deviation and confirmed
  operator-approved cables/connectors, accessible manual breakers on the NHR
  and supply, physical emergency-stop access and continuous local supervision.
- The manual breakers and emergency stop are physical fallbacks; software does
  not represent them as automatic interlocks or proof of electrical isolation.
- Approved immutable profile:
  `approved/phase2-tier-e-c1-cccv-v1.json`.
- Bundle digest:
  `sha256:3e1a046f0834688af69593d3780d16dd8fcdfb98cb364c4c20f66569ba1585f6`.
- Offline hardware-schema loading and service-registry reconstruction passed;
  the exact registered entry is available with no error and two stages.
- Focused workflow/workflow-service regression: `33 passed in 23.32s` using a
  fresh ASCII basetemp outside OneDrive. The preceding attempt produced 29
  setup errors from the already-known locked `.test-temp-final-2`; these were
  infrastructure errors, not functional test failures.
- `git diff --check` passed. No service start, IVI access, preflight or hardware
  command occurred during C0 preparation.
- C0 disposition: `PASS` by explicit operator approval. C0 does not authorize
  a workflow start. C1 requires a fresh immediate Tier E authorization after
  offline qualification, service startup, independent safe-state confirmation
  and physical preflight review.

### C1 initial physical preflight attempt - communication blocked

- Service started on 2026-08-26 at 11:13 local with physical backend
  `nhr-79503`, 10 Hz acquisition target and the exact approved C1 registry.
- Initial public runtime before preflight was disconnected/idle with no
  measurement and no acquisition. The C1 workflow was available with the exact
  approved digest.
- The physical preflight request blocked during IVI communication before a new
  durable preflight directory or report was created. No workflow run was
  created and no energizing start was requested.
- Normal Ctrl+C shutdown was requested. Service shutdown reported incomplete
  cleanup because the active preflight did not finish, then the process exited.
- Post-exit software inspection found no nhr-rt Python process and no listener
  on ports 9300/9400. This is not independent proof of physical safe state.
- Operator identified a laptop-to-NHR communication problem and elected to
  restart the laptop.
- Disposition: `BLOCKED`. Before any retry, independently verify output OFF,
  watchdog OFF, disabled channels, zero setpoints and no physical anomaly, then
  re-establish communication and perform a fresh C1 preflight. C1 remains
  unexecuted and requires a fresh immediate Tier E start authorization.

### C1 physical preflight after communication recovery

- Operator confirmed the communication issue resolved and the setup ready with
  NHR OFF, DUT connected and emergency-stop access.
- Service restarted cleanly with no stale nhr-rt process or port listener.
- Physical preflight `preflight-20260826T175007-2005d2f6` passed for exact C1
  digest and identity `DC PM 1` / `79503`.
- Requested/read-back safety limits matched: 10 A charge/discharge, 100 V
  charge maximum, 80 V discharge minimum and 1000 W charge/discharge, with
  0.1 s delays. The unwired NHR UUT temperature remains ignored.
- Initial and post-reconnect states were output OFF, watchdog OFF, channels
  disabled and setpoints zero. No emergency fallback was used.
- Post-preflight runtime was fresh at 88.672 V, acquisition healthy at 9.96 Hz
  and workflow idle. The approximately 157 degC unwired NHR temperature was
  excluded exactly as approved; external manual monitoring remains mandatory.
- Durable report:
  `runs/workflow-preflights/preflight-20260826T175007-2005d2f6/report.json`.
- C1 start remains unexecuted and requires immediate explicit Tier E approval.

### C1 v1 CCCV execution and disposition

- Immediate Tier E authorization received with external temperature 25 degC,
  emergency stop accessible and no anomaly.
- Run `fd23e1c0-e1af-4afb-8174-c0ad14965316` executed CCCV charge at 5 A with
  an 89.5 V target and 4.5 A cutoff for a maximum 60 s.
- Electrical behavior remained inside the approved envelope: 88.672 to
  88.950 V, maximum 5.014 A and maximum 445.81 W. The run added 0.0834 Ah and
  7.409 Wh with healthy 10 Hz acquisition.
- The module did not reach 89.5 V, so the CCCV cutoff never became eligible.
  The workflow correctly reported `condition_timeout`, skipped the following
  rest stage and used failure cleanup. The helper also sent a redundant stop
  when measurement became briefly unavailable during cleanup.
- The durable report records `passed: false`; cleanup and independent reconnect
  verified output/watchdog OFF, disabled channels and zero setpoints.
- Operator accepted the physical electrical behavior. Final operator check:
  output OFF, watchdog OFF, channels disabled, 25 degC externally and no
  anomaly.
- Disposition: `REPEAT`, not a hardware-safety failure. The approved correction
  is a new immutable 5 A constant-current 60 s stage followed by 10 s rest.

### C1 v2 constant-current repeat - software evidence

- Operator reviewed and approved reusable Tier E profile
  `phase2-tier-e-base-cc-5a-60s-v2` for C1-C6.
- Exact digest:
  `sha256:7f92b18a5fcdf49aa0ab5b8e5936bc722227517bd4abf08b3ac44afe5fb1607d`.
- Physical preflight `preflight-20260826T175917-a854da4f` passed with exact
  identity, limit readback and verified safe state.
- Immediate C1-v2 authorization received before start.
- Run `de6c857a-8c87-4f87-a9d6-cae1dcab780e` passed both stages by configured
  duration: 60 s constant-current charge and 10 s rest.
- Global evidence: 708 samples; 88.71 to 88.98 V; maximum 5.01 A and 445.91 W;
  0.08324 Ah and 7.397 Wh charged. No stop or emergency fallback was requested.
- The first rest sample retained a short transition residual (0.918 A / 135 W)
  before decaying to the known disabled residual range; the service report and
  final reconnect verified output/watchdog OFF, disabled channels and zero
  setpoints.
- Durable report:
  `runs/workflow-runs/de6c857a-8c87-4f87-a9d6-cae1dcab780e/report.json`.
- Operator independently confirmed after the run: physical output OFF,
  watchdog OFF, channels disabled, V/I/P setpoints zero, external temperature
  25 degC and no anomaly.
- Final C1 disposition: `PASS`.

### C2 simultaneous monitor observation - readiness gate

- C2 repeats the exact approved immutable v2 profile and digest used for the
  accepted C1 run; only the read-only observation load changes.
- The 64-bit read-only monitor is active on localhost port 9400 with a live
  browser page. It reports the service link live, workflow idle, physical
  output disabled, fresh measurement near 88.75 V and healthy acquisition near
  10 Hz. The page exposes no connect, disconnect, start, stop or setpoint
  controls.
- A dedicated C2 initiating client was syntax-checked in both 64-bit and
  32-bit environments. It supervises the approved 80-100 V, 10 A and 1000 W
  envelope while polling the monitor endpoint as the additional observation
  load.
- Physical preflight `preflight-20260826T180632-ada0ea94` passed for identity
  `DC PM 1` / `79503`, exact v2 digest, matching safety limits and verified
  final safe state. Post-preflight monitor state remained OFF/disabled and
  acquisition healthy.
- C2 start remains unexecuted and requires a fresh immediate Tier E
  authorization.

### C2 simultaneous monitor observation - software evidence

- Immediate Tier E authorization received with external temperature 25 degC,
  emergency stop accessible and ready, and no anomaly.
- Run `5fc6c907-d6fd-4394-bde7-62e14023d7bb` repeated the exact accepted C1 v2
  profile and passed both stages by configured duration: 60 s constant-current
  charge and 10 s rest.
- The active-stage CSV contains 605 steady-stage samples. Voltage remained
  88.817 to 89.019 V; maximum current was 5.014 A and maximum power 446.09 W,
  within the approved 80-100 V, 10 A and 1000 W safety envelope.
- The report records 0.08330 Ah and 7.405 Wh charged, no requested stop, no
  emergency fallback, no acquisition error and a verified/reconnected final
  safe state.
- The additional observer completed 64 successful monitor API observations
  without error. Monitor-observed acquisition rate remained 9.992 to 10.000 Hz
  and its sample counter advanced from 4723 to 5434. The live browser page
  displayed the correct run, stage, electrical values and final return to
  OFF/disabled.
- Durable report:
  `runs/workflow-runs/5fc6c907-d6fd-4394-bde7-62e14023d7bb/report.json`.
- Operator independently confirmed after the run: physical output OFF,
  watchdog OFF, channels disabled, V/I/P setpoints zero, external temperature
  25 degC and no anomaly.
- Final C2 disposition: `PASS`.

### C3 controlled stop under energy - readiness gate

- C3 uses the exact approved v2 profile and digest with no profile or safety
  limit change.
- The run-specific cooperative stop is preplanned at 20.0 s of elapsed time in
  the active 5 A charge stage. This minimizes additional charge while providing
  a stable energized interval before the stop request.
- Acceptance bound remains the operator-approved 5.0 s from the stop request
  to terminal verified safe state. The operator independently watches the
  physical transition and uses the physical emergency stop immediately for a
  missed bound or any uncertain/unexpected state.
- The dedicated C3 client was syntax-checked in both environments. It supervises
  the approved electrical envelope, sends only the run-specific stop request,
  measures the chronology and requires terminal `stopped`, no emergency
  fallback and verified final safe state.
- Physical preflight `preflight-20260826T181828-56dc0ad7` passed for exact
  identity/digest, matching safety limits and final safe state. The read-only
  monitor subsequently showed OFF/disabled, fresh measurement near 88.78 V and
  healthy acquisition near 10 Hz.
- C3 start remains unexecuted and requires a fresh immediate Tier E
  authorization.

### C3 controlled stop under energy - execution

- Immediate Tier E authorization received with external temperature 25 degC,
  emergency stop accessible and ready, and no anomaly.
- Run `6cd5bf19-1145-40d1-b998-1df1309aed54` reached the planned stop point at
  20.062 s in the active 5 A charge stage. At that point the service runtime was
  fresh at 88.998 V, 4.997 A and 444.78 W with healthy acquisition.
- The run-specific stop request returned HTTP 202 and the active routine stopped
  with reason `Routine stop requested`.
- Terminal `stopped` with verified/reconnected final safe state was observed
  8.109 s after the stop request. This exceeded the approved 5.0 s bound, so the
  service set `emergency_fallback_requested: true` and exposed the alert
  `Controlled workflow stop exceeded its approved timeout`.
- The durable workflow report records `passed: false`, sequence state `stopped`,
  `emergency_fallback_used: false`, output/watchdog OFF after cleanup, disabled
  channels, zero setpoints, successful reconnect and
  `final_safe_state_verified: true`. Thus fallback was requested by the service
  timeout policy but the report does not claim that the fallback action was
  used.
- Durable report:
  `runs/workflow-runs/6cd5bf19-1145-40d1-b998-1df1309aed54/report.json`.
- Software disposition: `FAIL` against the approved 5.0 s terminal-safe-state
  criterion. C4-C6 are blocked pending independent physical-state/timing
  confirmation and C3 disposition.
- Operator independently confirmed that the hardware is safe after C3. Exact
  physical transition time and whether the physical emergency stop was used
  remain to be confirmed; this safe-state confirmation does not by itself
  change the software `FAIL` disposition.
- Operator confirmed that no physical emergency stop was used. The observed
  hardware state is safe, and the operator considers approximately 8 s
  potentially acceptable but requested one diagnostic repeat to observe whether
  physical output disable occurs earlier than terminal workflow persistence.
- The repeat will keep the exact profile, limits and 5.0 s service policy. It
  does not pre-approve a revised acceptance bound or reclassify the first C3
  result.
- Diagnostic-repeat helper was syntax-checked in both environments and now
  provides short audible cues at 3, 2 and 1 s before the planned stop plus a
  distinct long cue starting at the stop request. These cues add no hardware
  authority or profile change.
- Recovery preflight `preflight-20260826T182644-cf078d8d` passed with exact
  identity/digest, matching safety limits and verified final safe state. The
  prior controlled-stop timeout remains visible as retained `last_error`; the
  instrument is currently OFF, watchdog OFF, channels disabled and setpoints
  zero with healthy acquisition.
- The C3 diagnostic repeat remains unexecuted and requires a new immediate
  Tier E authorization.

### C3 controlled stop diagnostic repeat - software evidence

- Immediate repeat authorization received with external temperature 25 degC,
  emergency stop accessible and ready, and no new anomaly.
- Run `15e19ff9-c39e-49c1-a225-0b9c2df414b4` repeated the exact v2 profile and
  stop point. Audible countdown cues were emitted at 3, 2 and 1 s before the
  stop, followed by the distinct stop-request cue.
- The planned stop request was issued at 20.032 s with fresh runtime at
  89.005 V, 4.998 A and 445.39 W. It returned HTTP 202 in 0.015 s.
- Terminal `stopped` with verified/reconnected final safe state was observed
  8.078 s after the request, reproducing the first C3 result. The service again
  set `emergency_fallback_requested: true` after its 5.0 s policy expired.
- Post-run software state is OFF with watchdog OFF, disabled channels, zero
  setpoints, fresh measurement and healthy acquisition. The prior timeout alert
  remains visible.
- Software result again exceeds the currently approved 5.0 s terminal bound.
  Final interpretation remains pending the operator's cue-referenced physical
  output-disable timing and confirmation of physical emergency-stop use.
- Operator did not hear the audible cues, but observed the physical system OFF
  at approximately 20 s on the timer measured from the beginning of charge.
  This coincides with the planned 20.0 s stop request and supports prompt
  physical output disable; it does not establish a precise request-to-OFF
  latency because the cue reference was unavailable.

### C3 v3 controlled-stop correction and readiness

- Operator confirmed no physical emergency-stop use during the diagnostic
  repeat and independently confirmed output OFF, watchdog OFF, channels
  disabled, V/I/P setpoints zero, external temperature 25 degC and no anomaly.
- Operator explicitly approved a revised 10.0 s controlled-stop terminal bound
  and immutable v3 workflow revision.
- New approved profile:
  `approved/phase2-tier-e-base-cc-5a-60s-v3.json`.
- Exact v3 digest:
  `sha256:eb7c64fc2e6fdf5ec83f5b76232edf84d3074a49be79c5243f8303f9295cb81c`.
- Automated comparison confirmed v2 and v3 have identical electrical stages,
  static safety values and workflow limits. Only the profile revision/name and
  stop-procedure timeout changed from 5.0 to 10.0 s.
- Offline registry reconstruction passed in both Python architectures. Focused
  workflow/workflow-service regression passed: `33 passed in 21.28s`. Two
  preceding attempts were non-functional environment failures caused by the
  known Python 32-bit/OneDrive temporary-path issue; the successful run used a
  fresh ASCII path outside OneDrive.
- The monitor and service stopped normally while the bench was safe. The
  service restarted with only the v3 workflow registered and no retained alert;
  the read-only monitor restarted and reattached.
- Physical preflight `preflight-20260826T183609-c5eac640` passed for exact v3
  identity/digest, matching safety limits and verified final safe state.
  Post-preflight monitor state is OFF/disabled with fresh measurement near
  88.80 V and healthy acquisition near 10 Hz.
- Historical v2 C3 results remain `FAIL` against their approved 5.0 s terminal
  bound and are not rewritten. C3 v3 requalification remains unexecuted and
  requires a fresh immediate Tier E authorization.

### C3 v3 controlled-stop requalification - software evidence

- Immediate Tier E authorization received with external temperature 25 degC,
  emergency stop accessible and ready, and no anomaly.
- Run `073c6c81-bada-4d61-b834-61cab30866eb` reached the planned stop point at
  20.078 s. Runtime at that point was fresh at 89.018 V, 4.999 A and 444.62 W
  with healthy acquisition.
- The run-specific stop returned HTTP 202. Terminal `stopped` with verified and
  reconnected final safe state was observed 8.203 s after the request, below
  the approved v3 10.0 s bound.
- Neither `emergency_fallback_requested` nor `emergency_fallback_used` was set.
  The report records the expected routine-stop sequence result, 0.02735 Ah and
  2.430 Wh charged, output/watchdog OFF after cleanup, disabled channels, zero
  setpoints and successful reconnect. As expected for an intentionally stopped
  workflow, the report field `passed` is false; C3 acceptance is based on the
  controlled `stopped` outcome and its timing/safe-state criteria.
- Durable report:
  `runs/workflow-runs/073c6c81-bada-4d61-b834-61cab30866eb/report.json`.
- C3 v3 software-evidence disposition: `PASS`. Final C3 disposition remains
  pending the operator's independent physical final-state and temperature
  confirmation.
- Operator independently confirmed physical output OFF, watchdog OFF, channels
  disabled, V/I/P setpoints zero, external temperature 25 degC, no physical
  emergency-stop use and no anomaly.
- Final C3 v3 disposition: `PASS`.

### C4 client-loss under energy - resumed readiness

- Campaign resumed on 2026-08-27 after the prior task stopped for lack of
  credits. No C4 workflow had been started before the interruption.
- Operator freshly confirmed the DUT remains connected to the NHR, physical
  output OFF, watchdog OFF, channels disabled, V/I/P setpoints zero, external
  temperature 25 degC, emergency stop accessible and no anomaly.
- Service and read-only monitor were restarted while the bench was safe. The
  service owns the exact approved v3 workflow
  `phase2-tier-e-base-cc-5a-60s-v3` with digest
  `sha256:eb7c64fc2e6fdf5ec83f5b76232edf84d3074a49be79c5243f8303f9295cb81c`.
- Fresh physical preflight `preflight-20260827T124549-e4294ea9` passed exact
  identity/digest, configured safety limits and initial/final safe-state
  verification. Durable report:
  `runs/workflow-preflights/preflight-20260827T124549-e4294ea9/report.json`.
- Post-preflight service and monitor snapshots independently report workflow
  idle, output OFF, channels disabled, V/I/P setpoints zero, fresh measurement
  near 88.75 V, healthy acquisition near 9.99 Hz and no alerts.
- The C4 initiator and observer helpers remain syntax-checked in both Python
  architectures. The initiator will start the exact v3 workflow and terminate
  only its own 64-bit client process after approximately 5 s of active charge.
  The service and read-only monitor remain running; a new observer will attach
  to the same run and supervise it to its normal 60 s charge plus 10 s rest
  termination.
- C4 remains unexecuted and requires a fresh immediate Tier E authorization.

### C4 client-loss under energy - software evidence

- Immediate Tier E authorization received with external temperature 25 degC,
  emergency stop accessible and ready, and no anomaly.
- Run `a368bc97-b946-459d-b200-121dc29155e7` started the exact approved v3
  workflow. The initiator deliberately exited only its own process after about
  5 s of active charge, while the run was still `running` at 88.922 V,
  5.003 A and 444.38 W with healthy acquisition and no alert.
- A new 64-bit observer retrieved the same run ID and supervised the
  service-owned workflow without restarting or replacing it. Charge remained
  within the reviewed 80--100 V, 10 A and 1000 W envelope.
- The 60 s constant-current stage passed on configured duration, followed by
  the 10 s rest stage. Terminal state and outcome were `passed`; no cooperative
  stop was requested and neither an emergency-fallback request nor fallback use
  occurred.
- Reported result was 0.08321 Ah and 7.399 Wh charged. Global acquisition was
  healthy at 9.9995 Hz effective rate, with 608 charge-stage samples and 102
  rest-stage samples.
- Cleanup and reconnect both verified output OFF, watchdog OFF, channels
  disabled and V/I/P setpoints zero. Service and independent read-only monitor
  snapshots then agreed on workflow idle, fresh measurement near 88.82 V,
  healthy acquisition near 9.99 Hz and no alerts.
- Durable report:
  `runs/workflow-runs/a368bc97-b946-459d-b200-121dc29155e7/report.json`.
- C4 software-evidence disposition: `PASS`. Final C4 disposition remains
  pending the operator's independent physical final-state, temperature and
  anomaly confirmation.
- Operator independently confirmed physical output OFF, watchdog OFF, channels
  disabled, V/I/P setpoints zero, external temperature 25 degC, no physical
  emergency-stop use and no anomaly.
- Final C4 disposition: `PASS`.

### C5 graceful service shutdown under energy - preparation

- C5 will reuse the exact approved v3 electrical profile and limits. The
  planned shutdown point is approximately 5 s into the 60 s charge stage.
- Only normal console `Ctrl+C` will be sent to the 32-bit service. No process
  kill or abrupt-loss mechanism is part of C5.
- The service is expected to request the active run's cooperative stop, verify
  cleanup, close acquisition and IVI, and persist the terminal run evidence.
- The service will remain stopped until the operator independently confirms the
  physical safe state. Recovery then uses a fresh service, retrieval of the
  same run and a fresh physical preflight.
- C5 remains unexecuted and requires a new immediate Tier E authorization.

### C5 graceful service shutdown under energy - shutdown evidence

- Immediate Tier E authorization received with external temperature 25 degC,
  emergency stop accessible and ready, and no anomaly.
- Fresh preflight `preflight-20260827T125336-63d8e044` passed for the exact v3
  identity/digest, safety limits and final safe state.
- Run `9653ab08-f4b5-443e-af67-06396a374634` reached the planned shutdown point
  after 5.0 s of charge. Runtime was fresh at 88.962 V, 4.997 A and 445.12 W,
  with healthy acquisition and no alerts.
- Normal console `Ctrl+C` was sent to the 32-bit service at
  `2026-08-27T12:53:56.998Z`. The service logged its normal operator-requested
  shutdown path and began closing resources; no process kill was used.
- The run persisted terminal `stopped` at `2026-08-27T12:54:04.851Z`, about
  7.853 s after the shutdown request and below the approved 10.0 s bound. Full
  service shutdown completed at `2026-08-27T12:54:05.299Z`, about 8.301 s after
  the request.
- The manifest records `stop_requested: true`, no emergency-fallback request,
  no fallback use, 0.01378 Ah / 1.222 Wh charged, no error, successful internal
  reconnect and verified final safe state. Cleanup/readback records output and
  watchdog OFF, channels disabled and all setpoints zero.
- Durable report:
  `runs/workflow-runs/9653ab08-f4b5-443e-af67-06396a374634/report.json`.
- Port 9300 refuses connections after shutdown, and the independent read-only
  monitor reports the expected HTTP 502 service-offline state. No service or
  monitor restart has occurred.
- C5 recovery is intentionally paused pending the operator's independent
  physical safe-state, temperature and anomaly confirmation.
- Operator independently confirmed before recovery: physical output OFF,
  watchdog OFF, channels disabled, V/I/P setpoints zero, NHR display `OFF`,
  external temperature 25 degC, no physical emergency-stop use and no anomaly.
- A fresh 32-bit service was then started. It recovered the same run as terminal
  `stopped`, with its stop request, verified safe state and lack of fallback
  preserved.
- Recovery preflight `preflight-20260827T130226-6d8a0b27` passed for the exact
  v3 identity/digest, safety limits and final safe state.
- After acquisition resumed, service and independent monitor snapshots agreed
  on workflow idle, output OFF, channels disabled, V/I/P setpoints zero, fresh
  measurement near 88.79 V, healthy acquisition near 10 Hz and no alerts.
- Final C5 disposition: `PASS`.

### C6 abrupt service loss/watchdog fallback - proposed preparation

- The unchanged v3 workflow enables the NHR watchdog and verifies its readback
  before entering the active sequence. C6 proposes the same approximately 5 s
  charge point used by C4/C5.
- Proposed loss mechanism: with PowerPanel closed, identify the sole running
  `.venv32/Scripts/nhr9300-service.exe` PID and forcibly terminate only that
  exact process. No `Ctrl+C`, cooperative stop or graceful cleanup will occur.
- A pre-loss JSON snapshot will be durably written before the forced loss,
  containing the run ID, exact workflow/digest and fresh active runtime.
- Proposed watchdog acceptance: physical output becomes OFF within 10.0 s of
  forced service termination. If output remains active at 10.0 s, or any state
  is uncertain or anomalous, the operator immediately uses the physical
  emergency stop/manual breaker. The watchdog may remain enabled and channels
  or setpoint values may remain latched after tripping; those are recovered
  only after physical output OFF is independently confirmed.
- Recovery remains prohibited until the operator reports the post-loss physical
  state. With the service still stopped and PowerPanel closed, a dedicated
  32-bit process then reads identity/status/watchdog, forces standby/output OFF,
  zeros and disables all channels/setpoints, disables the watchdog, verifies
  readback and closes IVI. Only after that does the service restart, recover the
  same run as `interrupted`, and perform a fresh preflight.
- C6 remains unexecuted. The loss mechanism, 10.0 s watchdog bound, recovery
  sequence and immediate Tier E run each require explicit operator approval.
- Operator separately approved the exact C6 loss mechanism: forced termination
  of only the verified 32-bit NHR service process, without `Ctrl+C`.
- Operator separately approved a 10.0 s maximum physical output-OFF watchdog
  bound and immediate physical emergency-stop/manual-breaker fallback at that
  bound or earlier for any uncertain/anomalous state.
- Operator approved the dedicated 32-bit direct-recovery sequence and confirmed
  PowerPanel closed. This mechanism approval does not authorize the energizing
  run; a fresh immediate Tier E authorization remains required.

### C6 abrupt service loss/watchdog fallback - loss evidence

- Immediate Tier E authorization received with DUT connected, physical output
  and watchdog OFF, channels disabled, V/I/P setpoints zero, external
  temperature 25 degC, emergency stop accessible and ready, and no anomaly.
- Fresh preflight `preflight-20260827T135649-f22f5a07` passed for the exact v3
  identity/digest, safety limits and final safe state.
- Run `8210055c-06fd-4ba0-b787-1acc984c7393` reached the reviewed loss point at
  about 5.094 s of charge. Runtime was fresh at 88.996 V, 4.994 A and 444.42 W,
  with healthy acquisition and no alerts.
- Durable pre-loss snapshot:
  `runs/PHASE2-PHYSICAL-20260825-NHR79503/tier_e_c6_pre_loss_8210055c-06fd-4ba0-b787-1acc984c7393.json`.
  Reaching the active workflow stage is downstream of the required watchdog
  enable/readback check in the approved hardware execution path.
- Immediately before loss, PID `5908` was reverified as the sole process whose
  executable path exactly matched `.venv32/Scripts/nhr9300-service.exe`.
- The approved forced termination, without `Ctrl+C`, was issued at
  `2026-08-27T13:57:24.759851Z`; the exact PID was absent by
  `2026-08-27T13:57:24.798882Z`.
- Service and monitor data endpoints became unavailable. The last durable run
  manifest remains non-terminal `running`, with no stop requested and final
  safe state unverified, which is the expected pre-recovery evidence for an
  abrupt process loss.
- No service, PowerPanel or IVI recovery process has been started. C6 is paused
  at the mandatory independent physical post-loss gate.
- Operator reported physical output OFF in less than approximately 10 s, NHR
  state `OFF`, watchdog still displayed ON with fault `Watchdog service
  timeout`, channels disabled, displayed V/I/P setpoints null, external
  temperature 25 degC and no anomaly.
- Operator corrected the preceding entry: no emergency stop or breaker was
  used. Physical output became OFF in less than approximately 10 s solely after
  the forced communication/service loss; the NHR displayed `OFF` and
  `Watchdog service timeout`. This satisfies the approved watchdog-output bound.
- The approved 32-bit direct recovery first connected read-only and observed
  identity `DC PM 1` / `79503`, output OFF, state OFF, channels/setpoints zero,
  watchdog OFF and measurement near 88.81 V. Its first attempt then stopped
  before any hardware write because the safe facade requires approved limits
  even for a standby-zero write. This was a recovery-tool guard failure, not a
  hardware-state failure.
- The recovery helper was corrected to recognize an already complete safe state
  and to program approved limits only when a recovery write is actually needed.
  Its repeat passed with the same complete safe readback; no recovery write,
  emergency stop or output action was necessary. Durable evidence:
  `runs/PHASE2-PHYSICAL-20260825-NHR79503/tier_e_c6_direct_recovery.json`.
- A fresh 32-bit service then recovered run
  `8210055c-06fd-4ba0-b787-1acc984c7393` as terminal `interrupted`, with
  `Service process ended before terminal persistence`, no stop request and the
  historical final safe state intentionally unverified.
- Recovery preflight `preflight-20260827T140234-3bf23e4d` passed for the exact
  v3 identity/digest, safety limits and final safe state. It cleared the
  recovery block without changing the recovered run's historical disposition.
- After acquisition resumed, service and monitor agreed on workflow idle,
  output OFF, channels disabled, V/I/P setpoints zero, fresh measurement near
  88.81 V, healthy acquisition near 9.99 Hz and no alerts.
- C6 software and watchdog-loss disposition: `PASS`. Final physical disposition
  remains pending the operator's post-recovery panel/fault confirmation.
- Operator independently confirmed post-recovery physical output OFF, watchdog
  OFF, channels disabled, V/I/P setpoints zero, NHR state `OFF`, watchdog fault
  cleared, external temperature 25 degC, no emergency-stop/breaker use and no
  anomaly. The NHR is reported ready for use.
- Final C6 disposition: `PASS`.

## Z1 - Tier E campaign closeout

- Final service and monitor snapshots agreed on workflow idle, output OFF,
  channels disabled, V/I/P setpoints zero, fresh measurement at approximately
  88.81 V, healthy acquisition at 10.0 Hz and no alerts.
- Operator independently confirmed physical output/watchdog OFF, NHR state
  `OFF`, fault cleared, external temperature 25 degC and no anomaly.
- Read-only monitor received normal `Ctrl+C` and logged operator-requested
  shutdown. The service then received normal `Ctrl+C` at
  `2026-08-27T14:06:46.787Z` and logged `NHR9300 service stopped` at
  `2026-08-27T14:06:47.638Z`, approximately 0.851 s later.
- Post-closeout checks found no `nhr9300-service` or `nhr9300-monitor` process,
  no listener on ports 9300/9400 and both local endpoints offline.
- All nine indexed C1--C6 key evidence files exist and their SHA-256 hashes are
  recorded in `TIER_E_CLOSEOUT.md`.
- C0--C6 final disposition: `PASS` for the exact approved Tier E contract.
- After complete process shutdown, the operator independently confirmed
  physical output/watchdog OFF, channels disabled, V/I/P setpoints zero, NHR
  state `OFF`, no displayed fault, external temperature 25 degC and no anomaly.
  The DUT remains connected intentionally in this verified safe state.
- Z1 final disposition: `PASS`. No Git mutation or publication was performed.
