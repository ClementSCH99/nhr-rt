# Software-only operator exercise

This fixture has explicit simulator approvals and no hardware backend. It is
separate from the non-approved template library. Do not convert it to IVI.

From the repository root, with the package installed in the active environment:

```powershell
python -m nhr9300.operator --config examples/operator-simulation/service.json --instrument-id sim-operator --service-url http://127.0.0.1:19300 --monitor-port 19400
```

Menu 1 / CONNECT launches the simulator service and HMI. Select `sim-rest` with
menu 3, preflight with 5 / PREFLIGHT, then start with 6 /
SUPERVISED_WORKFLOW_READY. The rest stage lasts five seconds. Menu 7 displays
finalized evidence while surveillance continues. Menu 9 resolves the completed
request. Repeat and use menu 8 during the run to exercise controlled stop.

Menu Q detaches the console and leaves these processes running. Their PIDs and
logs are printed at launch. The fixture writes under `runs/operator-simulation`
relative to the launch directory. CAN-PY is not required for this exercise.
