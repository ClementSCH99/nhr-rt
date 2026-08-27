# Operator proficiency exercise track

**Status:** next learning phase

**Goal:** make the operator autonomous from an empty console to a complete,
reviewable NHR run while preserving service ownership and safety gates.

## Working method

The operator performs the commands, edits and diagnosis. The assistant sets the
objective and acceptance criteria, explains concepts when needed, and reviews
the result. Assistance decreases through three passes:

1. **Guided:** explained commands; operator executes and describes the result.
2. **Prompted:** objective and hints only; operator chooses the commands.
3. **Independent:** operator plans and executes; assistant reviews evidence.

Simulator exercises come first. Training artifacts use `runs/learning/` and
never enter an approved physical registry automatically. No exercise carries
hardware, energizing or Git authorization into another exercise.

## Exercise sequence

| ID | Exercise | Demonstration |
|---|---|---|
| L1 | Environment and process map | Select 32-bit service versus 64-bit client/monitor; start and stop without an orphan process |
| L2 | Simulator lifecycle | Inventory, connect, observe, detach and prove that observers do not own acquisition |
| L3 | Profiles and immutable registry | Author `rest`, CC/CP/CCCV and CSV examples; validate them; compute a digest and diagnose drift |
| L4 | Workflow and evidence lifecycle | Preflight, start, observe, stop idempotently, then reconcile runtime, events, report and acquisition CSV |
| L5 | Fault and recovery lab | Diagnose wrong architecture, port conflict, bad resource, digest mismatch, stale measurement and client loss |
| L6 | Supervised physical capstone | Under fresh Tier P/N/E gates, perform identity, preflight, one approved run and independent final-safe-state proof |

L1–L5 are simulator/local exercises. L6 begins read-only/non-energizing and may
include one low-energy profile only after a new C0-style physical contract and
immediate run authorization. Charge and discharge are separate qualifications.

## Standard exercise card

For each exercise, record:

- objective and starting state;
- permitted environment and prohibited actions;
- operator plan and commands;
- one safe injected fault and recovery;
- expected versus observed ownership/state transitions;
- retained evidence and cleanup result;
- operator explain-back: why it worked, how failure is detected, and which
  process owned the instrument and evidence;
- disposition: `PASS`, `REPEAT` or `BLOCKED`.

An exercise passes only when the operator can reproduce the result and explain
the failure/recovery path. Completing L1–L6 produces the field-tested source
notes for a later `docs/HOW_TO_OPERATE_NHR_RT.md`.
