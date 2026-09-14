# Run recording and finalized evidence contract

Four lifetimes are intentionally independent:

| Object | Owner | End condition |
|---|---|---|
| Workflow | Service controller | Passed, stopped, failed or interrupted |
| Surveillance measurements | Service collector | Service shutdown or acquisition fault; may restart during cleanup |
| Session recording | Service controller | Closed after workflow cleanup, before terminal run publication |
| Service process | Operator | Explicit service shutdown |

`runtime.acquisition.evidence_path` is a **live surveillance CSV**. It may rotate
or keep growing after the workflow ends. Do not select it for a final merge.
Closing CAN-PY, the HMI or an observer does not stop this acquisition.

## Public API

The existing `GET /api/v1/instruments/{id}/workflow-runs/{run_id}` and public
`client.workflow_run()` return an additive `recording` object:

- `state`: `pending`, `recording`, `finalized`, `failed` or `interrupted`;
- `finalized`: true only after CSV closure, verified final safe state, hashing
  and successful atomic manifest persistence;
- `path`: absolute `session.csv` path, **not** permission to read a stable file;
- on success: `manifest_path`, `finalized_at_utc`, and `files`;
- on failure: `error`; no stable file list is advertised.

The workflow itself has an additional nonterminal `finalizing` state while
closed files are hashed/persisted. Keep waiting; this is not another energizing
step. A verified safe cleanup is not reclassified as a stop timeout merely
because evidence hashing takes time.

Each file entry has `role`, absolute `path`, `size_bytes` and `sha256` (hex).
The `session_measurements` role identifies the run CSV. Other roles identify
report, materialized profile, dynamic inputs, workflow sequence and stages.
The manifest adds `schema_version: 1`, `run_id` and `instrument_id`.

The session uses the acquisition CSV column names and UTC timestamps. It covers
the run lifecycle including available preflight/cleanup samples; measurement
gaps during reconnect remain gaps, never synthetic samples. A short run may
contain only a header. Nonempty/overlapping coverage is a separate merge check.

`runtime.workflow.last_run` retains the last run after the workflow becomes
idle. Consumers needing a particular run must use its ID, not assume the last
run is theirs. Existing `wait_workflow` and `stop_and_wait_workflow` return after
the finalization attempt. Timeout or transport loss does not prove closure.

## CAN-PY consumer sequence

1. Keep the selected NHR run ID with the CAN recording provenance.
2. If stopping early, request the approved workflow stop and poll its run.
3. Require terminal state **and** `recording.finalized == true`.
4. Read the manifest and select `session_measurements`; verify identity, size
   and SHA-256 before joining by UTC. Check sample coverage and clock offsets.
5. Write a separate derived merge; preserve every source on failure and report
   missing/empty/non-overlapping evidence explicitly.

Finalized does not mean the test passed: a controlled stop or failed test with
verified cleanup can have complete evidence. Conversely a passed sequence can
be followed by a recording failure; the run API is authoritative for overall
outcome/finalization. Its `report.json` describes the execution/cleanup result.

The session manifest excludes the live surveillance CSV and mutable
`run-state.json`. On service crash, an unfinished run is recovered as interrupted
with unfinalized evidence. Original files remain for diagnosis. No automatic
repair or merge is implied. File stability assumes no external modification;
consumers must verify the hashes.
