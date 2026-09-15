# Validation record

This is the single living index for validation status, future validation and
historical evidence. A result applies only to the exact software revision,
instrument, DUT, profile and conditions named by its evidence. `PASS` in one
row never authorizes another physical run.

## Current status

| Capability | Software / simulator | Physical evidence | Status / next gap |
|---|---|---|---|
| Typed facade, acquisition, workflow engine and evidence | Complete regression coverage; latest operator-consolidation baseline was 180 passed, 2 hardware tests skipped | Sessions 2-5 and 2026-08-19 refactor acceptance on NHR 79503 | Accepted within archived scope |
| Service authority and registered remote workflows | Milestones 1-4 plus operator runner covered in separate 32/64-bit processes | Phase 2 Tiers P/N/E on NHR 79503 | Accepted within exact archived configurations |
| Read-only runtime, SSE and monitor | Snapshot, queue-gap, reconnect, UI/error/stale-state and no-write coverage | Observed during Phase 2 campaign | Accepted as read-only; not a safety heartbeat |
| Finalized per-run recording | Normal, stop, failure, write failure, detached observer and repeated-run coverage | No dedicated new physical campaign after delivery | Software accepted; physical evidence workflow to be exercised on next approved run |
| External fail-closed interlocks | Snapshot validation, freshness, latching, controlled stop and fallback covered | No real CAN/BMS or sensor-chain validation | Software accepted; physical integration open |
| CAN-PY public integration | 64-bit publisher, retry/idempotence, capability discovery and lifecycle contract covered | No real CAN traffic or combined physical evidence | Boundary ready; end-to-end integration open |
| Dynamic SoP envelope | Not implemented | Not tested | Planned Milestone 6 |
| v0.3.0 integrated release | Partial inputs above | Not qualified as a complete release | Planned Milestone 7 |

The two routinely skipped tests are hardware-gated. A software suite reported
as green means they stayed skipped unless a specific physical record states
otherwise.

The current checkout was revalidated after this documentation consolidation on
2026-09-14: **181 passed, 2 skipped** in the 32-bit project environment. Only
documentation changed, so this confirms regression stability; it adds no new
physical evidence.

The current implementation has passed software, simulator and supervised NHR
tests for read-only acquisition, safety primitives, watchdog loss, CC, CCCV,
constant power, rest, ordered sequences and signed dynamic profiles.

The v0.2.0 refactor was also exercised with the simulator service running under
32-bit Python and `NHRServiceClient` under a separate 64-bit Python process.
Inventory, connect, SSE measurement, acquisition status, absolute CSV path and
disconnect completed successfully.

## Accepted behavior

- Safety-limit write/readback, arm lease, fresh-measurement gate and fail-closed
  interlocks.
- Charge/discharge CC with duration, voltage, capacity and energy termination.
- CCCV transition and current cutoff gated until CV entry.
- CP charge/discharge with duration, voltage, capacity and energy termination.
- Rest with output disabled.
- Global and per-stage CSVs with directional Ah/Wh totals.
- Dynamic zero without contactor cycling and direct charge/discharge transition.
- Cleanup and independent reconnect with output/watchdog disabled.

The active-zero residual documented in [the architecture safety
invariants](ARCHITECTURE.md#safety-invariants) is a known hardware behavior,
not a precise electrical zero guarantee.

## Final refactor acceptance — 2026-08-19

Commit `1a08bc6` was exercised on NHR serial `79503` with an approved five-stage
workflow: CCCV charge, rest, CP discharge, rest and signed power CSV. All stages
passed. CCCV stopped at 4.4950 A after CV activation, CP stopped at 88.7995 V,
and the dynamic profile reached its end. The global acquisition contains 1,226
samples over 122.578 s at 9.9988 Hz.

Cleanup and a separate-process reconnect both confirmed output off, watchdog
off, all channels disabled and all setpoint values zero. The operator confirmed
correct behavior with no abnormal observation. At the requested active 0 W
point, the measured residual was 35.3–40.4 W, within the already documented
active-zero behavior.

## Evidence registry

`archives/README.md` records immutable archive names and SHA-256 values. Raw
ZIP files are release assets rather than Git blobs. Historical narrative is
kept in the documentation-history archive so the active guides can describe the
current product without requiring session context.

Hardware tests are never inferred from simulator results. A release is marked
hardware-validated only after the exact approved profile, report, CSVs and final
safe-state evidence have been archived.

## Milestone 1 software validation — 2026-08-20

The service-authority implementation was validated without physical hardware.
The focused service/public-API selection passed 15 tests. The complete software
suite then passed 87 tests with the two hardware tests skipped.

The simulator coverage includes versioned and legacy read-route compatibility,
independent observer detach, persistence of service-owned acquisition after a
routine, primitive-control rejection for a physical-backend configuration, and
shutdown readback with output and watchdog disabled. This evidence does not
validate a physical shutdown delay, IVI behavior or an energizing workflow.

## Milestone 2 software validation — 2026-08-20

The approved-workflow service implementation was validated without physical
hardware. Focused tests cover immutable JSON/CSV bundle digests, registry drift,
remote-control policy, the two physical-start gates without connecting IVI,
preflight, asynchronous start, request idempotence, one-run exclusivity,
progress, run-owned CSV evidence, idempotent cooperative stop, forced failure,
interrupted-manifest recovery and graceful service shutdown.

A separate 64-bit Python 3.12 process completed preflight, start, polling and a
simulated workflow against the service test process running under 32-bit Python.
The complete software suite passed 98 tests with the two hardware tests skipped.

No physical workflow, fallback timeout, IVI reconnect or hardware shutdown
behavior was validated for Milestone 2. Physical execution remains disabled
unless a local registry, per-instrument remote enable, per-run acknowledgement
and explicitly approved controlled-stop timeout are all present.

## Milestone 3 software validation — 2026-08-21

Runtime observability was validated without physical hardware. Eight focused
tests cover the disconnected read-only snapshot, live consolidated measurement
and evidence state, acquisition-error alerts, independent SSE viewers, bounded
slow-viewer queues with explicit dropped-event counts, publication-error
isolation, bounded broker shutdown and HTTP SSE EOF during service shutdown.

The final complete software suite passed 106 tests with the two hardware tests
skipped. Existing service-authority, workflow-registry, separate-process M2 and
simulator safety regressions remained green. Python compilation and Git diff
whitespace checks also passed.

This evidence does not validate IVI timing, physical NHR behavior, real 32/64-bit
Milestone 3 viewer deployment, external CAN/BMS sources or dynamic SoP limits.
The external-source and dynamic-limit snapshot positions intentionally report
`not_configured` until Milestones 5 and 6 implement and validate them.

## Milestone 4 software validation — 2026-08-21

The separate read-only web monitor was validated without physical hardware.
Fifteen focused tests cover locally packaged assets, the read-only display
configuration, transparent runtime fixtures, rejection of every supported HTTP
write method, visible service failure, opening/refreshing/closing without a
simulator state change, bounded configuration and localhost-only service URLs.

The final complete software suite passed 121 tests with the two hardware tests
skipped. An earlier complete run had one intermittent failure in the existing
Milestone 2 separate-64-bit-process workflow test; it passed immediately in
isolation and passed again in the final complete run. Python 3.12 64-bit also
imported the monitor, loaded its packaged assets and bound a localhost server
without IVI or third-party monitor dependencies.

Desktop visual QA at 1440 x 900 covered:

- disconnected/unavailable values without converting API `null` counters to
  zero;
- live simulator values, fresh measurement status, 5 Hz acquisition and the
  bounded trend buffer;
- stale measurement, stale interlock, enabled output, acquisition error and
  static power ceilings from an API fixture;
- service loss with the link marked unavailable, retained values labelled as
  retained and measurement freshness changed to unknown;
- absence of buttons, forms, inputs, selectors or other control affordances.

The browser used only assets served from localhost. This software evidence does
not validate physical NHR behavior, IVI timing, energized-test interference,
external safety heartbeat handling or a dynamic SoP ceiling. Those remain
future supervised M5 validation and Milestone 6 implementation items.

## Expansion Phase 2 physical validation — 2026-08-25 to 2026-08-27

Milestones 1–4 completed the planned simulator and supervised physical campaign
on NHR serial `79503` (`DC PM 1`). T0, A1–A6, B1–B5, C0–C6 and Z1 received
final `PASS` dispositions. The campaign covered independent observers,
primitive-control rejection, runtime/monitor consistency, approved remote
workflow ownership, controlled stop, initiating-client loss, graceful service
shutdown and abrupt service loss with watchdog fallback.

The final Tier E contract was limited to the reviewed 24s2p NMC module and the
approved 5 A charge workflow: 80–100 V, 10 A and 1000 W campaign bounds; 60 s
charge followed by 10 s `rest`; and a 10 s controlled-stop/watchdog bound. The
operator independently confirmed the final safe state: output and watchdog off,
channels disabled, V/I/P setpoints zero, state `OFF`, no displayed fault and no
physical anomaly.

This acceptance does not cover discharge, another DUT or profile, unattended
operation, external CAN/BMS fail-closed interlocks, dynamic SoP limiting or a
general product release. The closeout archive is indexed in
`archives/phase2-physical-validation-20260825/README.md`. Raw generated runs and
local approved profiles were deliberately removed after the Markdown evidence
was archived.

## Milestone 5 software validation — 2026-09-09

External fail-closed interlocks were implemented and validated without CAN or
physical hardware. Thirteen focused tests cover finite numeric and boolean
rules, strict snapshots, missing/unhealthy/stale/out-of-order data, runtime
latching with exact triggering-snapshot evidence, safe-permissive stability,
bounded emergency fallback, bounded request/source/signal cardinality, a
stable SSE payload, strict stop/error classification, API publication,
pre-start refusal, runtime heartbeat loss, runtime voltage trip, controlled
cleanup and durable stop evidence. The timing-sensitive M5 group passed twice
consecutively after both the acquisition and routine detection paths were
routed through the same workflow controller.

The final post-audit complete software suite passed 141 tests with the two
hardware tests skipped. Python compilation and `git diff --check` also passed.
One earlier complete run encountered the existing intermittent Windows monitor socket
failure (`WinError 10053`); that test passed in the focused rerun and in the
final complete suite.

This evidence proves the service/simulator contract only. It does not validate
a real CAN transport, DBC/signal identity, timestamp accuracy, publication
cadence, units, sensor wiring or placement, reviewed battery thresholds,
physical NHR controlled-stop time, emergency fallback time, or final physical
safe state. No hardware command, energization, commit or push was performed.

The exact snapshot/rule contract and operator boundaries are consolidated in
the external-snapshot section of [the operating guide](HOW_TO_OPERATE_NHR_RT.md).

## CAN-PY integration readiness — 2026-09-10

Six software-only integration blocks hardened idempotent snapshot retry,
published compatibility metadata and stable error codes, added a synchronous
single-owner snapshot publisher, supplied a reference JSON Lines producer, and
defined the CAN-PY ownership/lifecycle contract. No CAN-PY file was modified.

Focused acceptance covers identical and conflicting retries, ambiguous
transport recovery, API-rejection resynchronization, publisher restart,
contract discovery, bounded SSE queues/EOF and the interruptible default
reconnect backoff (`0.5, 1, 2, 5` seconds, capped at 5 seconds). A separate
Python 3.12 64-bit process loaded the dependency-free public client without
`comtypes`, verified the external-snapshot 1.0 contract and published a snapshot
through the 32-bit simulator service.

The final complete software suite passed 148 tests with two hardware tests
skipped. Compilation of `src`, `tests` and `examples`, public-import checks,
documentation-link checks and `git diff --check` passed. One grouped run saw
the known intermittent Windows socket `WinError 10053` on the oversized-body
test; that test then passed three consecutive isolated runs and the final suite.

This validates the NHR-RT integration boundary, not real CAN traffic, DBC or
signal identity, sensor timestamps/units, production cadence, physical stop
timing, dynamic SoP or safe state on hardware. Those remain separate CAN-PY,
M6 and supervised physical-validation responsibilities.

## Operator consolidation — 2026-09-14

The finalized-session recording, guided operator console, monitor improvements
and non-approved example catalogue were validated without physical hardware.
The complete 32-bit suite passed 180 tests with two hardware tests skipped.
Compilation and whitespace checks passed, and the entry point was exercised
from 64-bit Python.

The integrated simulator path covered service and monitor process launch,
preflight, start, guided stop, session finalization and continued surveillance.
Tests also covered finalization after normal completion, repeated stop, failure,
write failure, detached observers, second runs, bundle drift, port conflicts,
wrong-service attachment and recovery of an uncertain start with the same
idempotence key. Visual fixtures covered missing, constant and interrupted
trends, a latched interlock, finalization and service loss.

This did not validate operator usability on the actual bench, emergency-stop or
inhibit contacts, IVI behavior, a physical profile, CAN-PY merge behavior or a
physical final safe state. The subsequent evidence-recovery hardening is
covered by repository regression tests but has not received a separate physical
campaign.

## Planned validation

### Milestone 6 — dynamic SoP envelope

The intended contract is that fresh external charge/discharge capability may
only reduce the effective power ceiling. The applied ceiling is the minimum of
the approved workflow ceiling, NHR safety limit and fresh external SoP value.
It never rewrites or increases approved NHR safety limits.

Implementation is not authorized by this plan. Before coding, freeze the
contract and architecture. Software acceptance must demonstrate:

- rising, falling, missing, invalid and stale SoP in the simulator;
- applied setpoints never exceeding workflow, hardware or external ceilings;
- CC, CCCV, CP and dynamic-profile compatibility without relay/state cycling;
- unchanged legacy regulation behavior;
- controlled stop and durable evidence when required SoP becomes unavailable;
- source, requested, approved and applied power values for material changes.

Physical status remains `NOT TESTED`. It requires separately reviewed signals,
units, timestamps, cadence, thresholds, profiles, stop timing and immediate
per-run authorization.

### Milestone 7 — integrated validation and v0.3.0

Software and simulator scope:

1. registry, bundle digest, double gates and primitive-write rejection;
2. run idempotence, exclusivity, stop, restart and evidence recovery;
3. external value, timestamp, sequence, freshness, latching and rule behavior;
4. SoP clamping and regulation-mode compatibility;
5. SSE lifecycle, bounded queues, EOF/reconnect and monitor visual QA;
6. separate 32-bit service and 64-bit client/monitor integration;
7. complete regression suite with environmental cleanup errors reported apart
   from functional failures.

Proposed supervised physical sequence, subject to a new approved protocol:

1. remote preflight without energizing;
2. short reviewed workflow from the 64-bit client;
3. unsafe isolation permissive blocking start;
4. reviewed voltage or temperature trip requesting controlled stop;
5. measurable SoP reduction below approved static limits;
6. heartbeat loss and bounded emergency fallback;
7. simultaneous read-only monitoring without interference;
8. cleanup and independent reconnect confirming output/watchdog off and every
   channel/setpoint disabled or zeroed.

The physical evidence package must retain exact revisions, configurations,
profiles and digests, external snapshots, event chronology, reports, finalized
CSV/manifests, operator observations, deviations and independent final-state
proof. Temperature cannot be accepted until signal identity, aggregation,
units, timestamps, sensor placement and wiring are verified.

Release requires architectural review, explicit approval of every profile,
threshold and timing value, software and supervised evidence, operator
acceptance, archive review, and separate authorization to commit, tag, push and
publish.

## Evidence retention rules

- Keep `docs/VALIDATION.md` as the status/index; do not paste full run logs here.
- Put immutable campaign narratives and closeouts under `archives/<campaign>/`.
- Publish large raw ZIP evidence as a release asset and record its SHA-256 in
  [the archive index](../archives/README.md).
- Retain exact revision, test ID, setup, profile/digest, result, deviations,
  limitations and final safe-state evidence for every physical disposition.
- Preserve failed evidence when it explains a corrected limit, timeout or
  procedure. Remove transient duplicates only after the retained record is
  independently sufficient.
