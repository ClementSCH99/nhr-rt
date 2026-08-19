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

## Evidence registry

`archives/README.md` records immutable archive names and SHA-256 values. Raw
ZIP files are release assets rather than Git blobs. Historical narrative is
kept in the documentation-history archive so the active guides can describe the
current product without requiring session context.

Hardware tests are never inferred from simulator results. A release is marked
hardware-validated only after the exact approved profile, report, CSVs and final
safe-state evidence have been archived.
