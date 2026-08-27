# Expansion Phase 2 validation archive

**Campaign:** `PHASE2-PHYSICAL-20260825-NHR79503`

**Dates:** 2026-08-25 to 2026-08-27

**Final disposition:** `PASS` for the reviewed Milestones 1–4 scope

## Retained records

| File | Purpose |
|---|---|
| `PHASE2_PHYSICAL_VALIDATION_PLAN.md` | Frozen execution, safety and evidence contract used by the campaign |
| `T0.md` | Software, simulator, client and monitor baseline |
| `TIER_N_CLOSEOUT.md` | Non-energizing B1–B5 closeout |
| `TIER_E_CLOSEOUT.md` | Energizing C0–C6 and Z1 closeout, key run IDs, results and deviations |
| `validation_log.md` | Detailed ordered campaign chronology and operator confirmations |

The plan is historical and no longer an active authorization document. The
closeouts and validation record describe the accepted result and its boundary.

## Closeout clean

The following transient artifacts were deliberately removed on 2026-08-27:

- generated acquisition CSVs, workflow/preflight JSON reports and caches;
- all local approved-profile revisions;
- the local hardware service configuration referencing those profiles;
- one-off Tier S/P/N/E campaign runner and recovery scripts.

The removed `runs/` tree contained 236 files (about 32 MB). The retained records
preserve the relevant contract, run identifiers, digests, measured results,
deviations and final-state confirmations. Hashes that appear in historical
closeout records identify the evidence as captured during execution; their raw
files are no longer retained in this repository.

## Validation boundary

Acceptance is limited to the exact configurations recorded in the closeouts.
It does not approve another DUT/profile, discharge, unattended operation,
external CAN/BMS fail-closed interlocks, dynamic SoP limiting or general product
release. New physical work requires a fresh reviewed contract and authorization.
