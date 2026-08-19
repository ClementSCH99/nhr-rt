# Workflow profiles

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
