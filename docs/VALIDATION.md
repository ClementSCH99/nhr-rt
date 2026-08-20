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
