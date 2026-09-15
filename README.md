# NHR Remote Testing

NHR Remote Testing (`nhr9300`) is a safety-oriented Python toolkit for NH
Research NHR9300 cyclers. It provides typed instrument access, reusable battery
test workflows, measurement acquisition, a localhost service for 64-bit
clients, and a deterministic simulator.

## Start here

The active documentation has three entry points:

1. [Architecture and design](docs/ARCHITECTURE.md) — ownership, module roles,
   safety invariants, data flow, extension boundaries and trade-offs.
2. [How to operate NHR-RT](docs/HOW_TO_OPERATE_NHR_RT.md) — installation,
   profiles, service configuration, CLI commands, Python API, monitoring,
   external snapshots, evidence and troubleshooting.
3. [Validation status and plan](docs/VALIDATION.md) — what has been tested,
   what remains unverified, the future validation gates and the evidence index.

Historical validation evidence is kept under [archives](archives/README.md).
Files under `examples/` are learning templates only: their identities, limits,
timeouts and approval flags are not approved for physical use.

## Architecture in one minute

```text
64-bit client / monitor / CAN-BMS adapter
                    |
             localhost HTTP/SSE
                    |
      32-bit nhr9300 service + workflow engine
                    |
        NHR9300 safety facade + backend
                    |
            simulator or IVI-COM
```

The 32-bit service is the sole owner of IVI-COM, the physical NHR connection,
acquisition, workflows and the final safe state. Clients request registered
operations and observe state; they never own the hardware. `NHR9300` is the
only supported path to a backend and enforces limits, arm leases, fresh
measurements, interlocks and cleanup.

## Installation

Python 3.12 or newer is required. Physical IVI-COM access requires 32-bit
Python on Windows.

```powershell
# Development, simulator and tests
python -m pip install -e ".[test]"

# Physical service environment
.\.venv32\Scripts\python.exe -m pip install -e ".[ivi]" --no-build-isolation
```

## First safe run

Copy and review an example, then run it in simulation:

```powershell
.\.venv32\Scripts\nhr9300-run.exe --simulate `
  --profile .\examples\workflows\sequence.example.json
```

For the guided two-terminal simulator experience:

```powershell
.\.venv32\Scripts\nhr9300-operator.exe `
  --config .\examples\operator-simulation\service.json `
  --instrument-id sim-operator
```

The complete commands and configuration formats are in the
[operating guide](docs/HOW_TO_OPERATE_NHR_RT.md).

## Safety boundary

Software tests and simulation are not physical validation. Physical execution
requires an engineering-reviewed exact profile and digest, approved limits and
stop policy, the correct resource and serial, fresh preflight evidence, an
operator at the bench, an accessible emergency stop and explicit authorization
for that run. A client exception, HTTP timeout or closed UI is never proof that
the output is off.

## Development check

```powershell
.\.venv32\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp="$env:TEMP\nhr-rt-tests"
```

See [validation status and plan](docs/VALIDATION.md) before interpreting a pass
as software, simulator or physical evidence.
