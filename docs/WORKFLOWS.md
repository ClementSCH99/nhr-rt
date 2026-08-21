# Workflow profiles

## Service registry

Remote clients cannot submit workflow files. The service configuration points
to reviewed local bundles:

```json
{
  "workflow_registry": [
    {
      "workflow_id": "approved-id",
      "instrument_id": "nhr-79503",
      "profile_path": "approved/workflow.json",
      "expected_resource": "DC PM 1",
      "expected_serial_number": "79503",
      "expected_bundle_digest": "sha256:REVIEWED_DIGEST",
      "approved": true
    }
  ]
}
```

The digest is SHA-256 over a canonical manifest. The manifest contains the
logical path, byte size and SHA-256 of `workflow.json` and every referenced CSV,
sorted by path. Changing JSON whitespace, CSV line endings or any dynamic value
changes the bundle digest. Files are snapshotted at startup and disk drift is
rejected rather than hot-loaded.

`WorkflowBundle.load(path, hardware=...)` computes the digest for review. The
computed value does not itself approve a profile; the registry flag, embedded
profile approvals and identity fields remain independent gates.

A physical instrument also requires a separately reviewed stop policy before
start is permitted:

```json
"controlled_stop_policy": {
  "timeout_s": 3.0,
  "approved": false,
  "profile_name": "REPLACE_WITH_REVIEWED_POLICY"
}
```

The values above are illustrative and unapproved. The software supplies no
physical default and does not infer that any timeout is safe.

## Service run lifecycle

The versioned endpoints are:

- `GET /api/v1/instruments/{id}/workflows`
- `POST /api/v1/instruments/{id}/workflow-runs/preflight`
- `POST /api/v1/instruments/{id}/workflow-runs`
- `GET /api/v1/instruments/{id}/workflow-runs/{run_id}`
- `POST /api/v1/instruments/{id}/workflow-runs/{run_id}/stop`

Run states move through `accepted`, `preflighting`, `running`, optional
`stop_requested`, and a terminal `passed`, `stopped`, `failed` or `interrupted`
state. The snapshot reports stage, step, termination target, truthful available
progress, live directional Ah/Wh totals, report path, error and independently
verified final safe state.

One approved workflow or preflight may own an instrument at a time. A repeated
start with the same request UUID and bundle returns the original run. Stop is
idempotent. Non-terminal manifests found after restart become `interrupted` and
block new starts until a successful preflight verifies the runtime again.

`nhr9300-run` executes a validated JSON workflow in simulation or on a
supervised physical module. Supported stages are constant current, CCCV,
constant power, rest and signed current/power CSV profiles.

```powershell
.\.venv32\Scripts\nhr9300-run.exe --simulate `
  --profile .\examples\workflows\sequence.example.json
```

Hardware execution additionally requires the reviewed local copy, the expected
resource, and operator acknowledgement:

```powershell
$env:NHR9300_RESOURCE = "DC PM 1"
$env:NHR9300_WORKFLOW_ACK = "SUPERVISED_WORKFLOW_READY"
.\.venv32\Scripts\nhr9300-run.exe --hardware --preflight-only `
  --profile .\path\approved-workflow.local.json
```

Remove `--preflight-only` only while the operator is present and the emergency
stop is accessible.

## Limits and termination

Global `safety_limits` are always programmed on hardware. Per-stage enable
flags select the active control/limiting channels:

- CC requires current control and a voltage limit;
- CCCV additionally requires `cutoff_current_a`;
- CP requires power control and a voltage limit; current limiting is optional;
- dynamic current/power profiles require the corresponding control channel and
  charge/discharge voltage limits;
- each active profile must retain at least one valid operational limit.

CP and CC stages may stop by duration, voltage, capacity or energy. CCCV cutoff
is evaluated only after the CV voltage has been reached. A sequence stops at
the first failed stage.

## Evidence

Every execution writes a JSON report, one global acquisition CSV and one CSV
per stage. The report records the exact JSON and dynamic CSV paths, SHA-256,
identity, requested/read-back limits, termination, acquisition statistics,
directional Ah/Wh and independently observed final state.
