# NHR Remote Testing

NHR Remote Testing (`nhr9300`) is a safety-oriented Python toolkit for NH
Research NHR9300 cyclers. It provides typed hardware access, acquisition,
reusable battery-test workflows, a localhost service for 64-bit clients and a
simulator for software validation.

## Design

- One 32-bit process owns IVI-COM and each physical NHR.
- `NHR9300` serializes backend calls and enforces limits, arm leases, fresh
  measurements, interlocks and safe cleanup.
- A dependency-free `NHRServiceClient` allows 64-bit applications to consume
  measurements without loading the driver.
- Workflows support CC, CCCV, constant power, rest, ordered sequences and signed
  current/power CSV profiles.
- Acquisition produces a global CSV, per-stage CSVs and machine-readable JSON
  evidence.

See [Architecture](docs/ARCHITECTURE.md) and [Safety](docs/SAFETY.md) before
adding a hardware command path.

For task-oriented setup, simulation, service/client examples, workflow control,
evidence interpretation and diagnostics, use the
[complete operating how-to](docs/HOW_TO_OPERATE_NHR_RT.md).

## Installation

Python 3.12 or newer is required. The physical driver requires 32-bit Python on
Windows.

```powershell
# Software development and simulation
python -m pip install -e ".[test]"

# Physical NHR service in the 32-bit environment
.\.venv32\Scripts\python.exe -m pip install -e ".[ivi]" --no-build-isolation
```

## Simulate a workflow

Copy an example and review its values. Repository examples are intentionally
unapproved and cannot be used directly for hardware activation.

```powershell
.\.venv32\Scripts\nhr9300-run.exe --simulate `
  --profile .\examples\workflows\sequence.example.json
```

The runner validates the profile before creating its timestamped output folder.
See [Workflow profiles](docs/WORKFLOWS.md) for the complete contract.

## Run the localhost service

```powershell
.\.venv32\Scripts\nhr9300-service.exe `
  --config .\examples\service.hardware.example.json
```

The service listens on `127.0.0.1:9300` by default. Keep it local; it has no
network authentication boundary. See [64-bit client integration](docs/CLIENT_INTEGRATION.md)
and the [service authority contract](docs/SERVICE_AUTHORITY.md). Read-only
applications use the consolidated [runtime observability contract](docs/RUNTIME_OBSERVABILITY.md).

## Run the read-only web monitor

Install the base package in a standard 64-bit Python environment while the
32-bit service remains separate, then select one configured instrument:

```powershell
nhr9300-monitor --instrument-id nhr-79503
```

Open `http://127.0.0.1:9400`. The monitor is localhost-only, works without an
internet connection and exposes no NHR control. See the
[monitor contract and operating guide](docs/MONITOR.md).

## Python API

```python
from nhr9300 import NHRServiceClient

client = NHRServiceClient("http://127.0.0.1:9300")
print(client.instruments())
```

Hardware access and workflow execution are also available as typed APIs, but
they retain the same approval and safety gates as the CLI.

The versioned service can execute only immutable, locally registered workflow
bundles. A 64-bit client supplies a `workflow_id`, expected digest and per-run
acknowledgement; it cannot upload profiles or issue arbitrary hardware
commands. See [64-bit client integration](docs/CLIENT_INTEGRATION.md).

## Development and validation

```powershell
.\.venv32\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp="$env:TEMP\nhr-rt-tests"
```

- [Development guide](docs/DEVELOPMENT.md)
- [Validation record](docs/VALIDATION.md)
- [Operating how-to](docs/HOW_TO_OPERATE_NHR_RT.md)
- [Operator learning findings and improvement backlog](docs/OPERATOR_LEARNING_FINDINGS.md)
- [Operator proficiency exercises](docs/OPERATOR_PROFICIENCY_EXERCISES.md)
- [Evidence registry](archives/README.md)
- [Archived Phase 2 validation plan](archives/phase2-physical-validation-20260825/PHASE2_PHYSICAL_VALIDATION_PLAN.md)
- [Future roadmap](ROADMAP.md)

Software tests do not constitute hardware validation. Physical execution always
requires an approved exact profile, operator supervision and an accessible
emergency stop.
