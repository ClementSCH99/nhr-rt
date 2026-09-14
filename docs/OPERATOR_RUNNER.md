# Guided operator runner

Run the console with the 64-bit environment containing this package. Use the
32-bit environment for the IVI service. After installing the updated package:

```powershell
nhr9300-operator --config service.hardware.local.json --instrument-id YOUR_ID --service-python .\.venv32\Scripts\python.exe
```

From a source checkout, `python -m nhr9300.operator` accepts the same arguments.
Use `--service-url http://127.0.0.1:PORT` and `--monitor-port PORT` for alternate
ports. The default service port is 9300; the monitor port is 9400. CAN-PY is
started separately in the second terminal. No command is sent at console entry.

## First preparation

1. Review a copy of a [non-approved example](../examples/workflows/README.md).
   Supply the exact identity, bench procedure, safety/workflow limits and
   approvals through the existing review process. The runner cannot approve.
2. Select the corresponding local registry workflow (menu 3).
3. Prepare (4): validate the bundle and review the proposed digest-only semantic
   change. Type `APPLY` to save. No change is written if validation fails.
4. Launch/join (1). Starting a service requires `CONNECT` because startup connects
   configured instruments. Existing services must match the selected config path.
   The runner displays child PIDs and log paths and opens the read-only HMI.
5. If the config changed, explicitly shut down/restart the service at a safe
   opportunity using the existing service procedure. The runner never restarts
   a shared service automatically. Use the displayed PID/log to identify it;
   do not terminate a process as a substitute for stopping an active test.

## Everyday test

Select the workflow, run preflight (5, type `PREFLIGHT`), inspect the result, then
start (6, type `SUPERVISED_WORKFLOW_READY`). Preparation is unnecessary when the
approved files/configuration are unchanged. The runner still validates the
local bundle against the running registry and the service repeats its gates.

Preflight reads identity, configures/checks reviewed limits and verifies cleanup;
it is not merely an offline lint. Check physical emergency stop and inhibit
before preflight. The current IVI status has no qualified signal for those
contacts, so the diagnostic says unknown. Output DISABLED alone is normal.

Menu 7 shows state and evidence; the HMI provides continuously refreshed values.
Menu 8 requests cooperative stop, waits up to 60 seconds and displays the
finalization result. Timeout leaves the service responsible for the run: inspect
again, do not infer a physical stop from a client error.

After normal completion use menu 9 to retrieve the completed request and clear
its journal before the next test. With an active request it only observes when
the run ID is known. If the start response was lost, it requires `RETRY` before
resending the **same** request UUID; this can start a request never accepted by
the service. It never silently creates another UUID for an uncertain start.

`Q`, EOF and Ctrl+C detach the console. Service, monitor and any active workflow
remain alive. The exit message states this explicitly. Relaunch with the same
config/instrument to inspect or stop. Logs/journal live in `operator-logs` beside
the config. Treat a journal as unresolved until recovered; do not delete it to
work around an uncertain start. Run one console per instrument/configuration.

## Reading the display

- `03_wait` is an indexed engine step: the test is active under surveillance.
  Rest waits for duration; CC/CP can wait for a condition; CCCV applies the
  current cutoff after the voltage activation condition. A condition timeout
  fails the stage. The tooltip retains the technical identifier.
- Time progress is elapsed time against the reviewed bound, not a predicted
  percentage of battery completion. Condition metrics are displayed separately.
- Interlock margin is signed distance in the displayed unit to the nearest
  configured boundary. It is not a new warning threshold. A latched trip keeps
  its original value even if a newer source value returns within limits.
- Trends have separate V/A/W scales and a shared UTC time axis; long sample gaps
  are not connected. They are bounded display history, not exported evidence.
- Surveillance CSV and session evidence have distinct labels. Only finalized
  session evidence is intended for the downstream merge.

See [SESSION_EVIDENCE.md](SESSION_EVIDENCE.md) for file roles and failure behavior.
CAN-PY automatic stop/merge integration is a separate dependency.
