# Validation record

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

The active-zero residual documented in `SAFETY.md` is a known hardware behavior,
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

The exact snapshot/rule contract and operator boundaries are documented in
[EXTERNAL_INTERLOCKS.md](EXTERNAL_INTERLOCKS.md).
