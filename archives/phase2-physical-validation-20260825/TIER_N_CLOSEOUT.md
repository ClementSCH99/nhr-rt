# Phase 2 Tier N Closeout

> Archived campaign record. Referenced raw files and approved profiles were
> removed during closeout; see [archive notes](README.md).

**Campaign:** `PHASE2-PHYSICAL-20260825-NHR79503`

**Instrument:** `DC PM 1`, serial `79503`

**Final disposition:** `PASS`

## Active validated contract

- Workflow: `phase2-tier-n-no-dut-rest-v3`
- Bundle digest:
  `sha256:5e14ae28b0c4c21c3c87dc082e90cab88f526211866fd0a1a2ab6121623099ea`
- No DUT; one 30 s `rest` stage; no output enable
- Controlled-stop bound: 5.0 s
- Charge limits: 5 A, 100 V, 500 W
- Discharge limits: 5 A, 0 V minimum, 500 W

## Results

| Test | Result | Key evidence |
|---|---|---|
| B1 | PASS | v3 physical preflight `preflight-20260825T200701-be43d48e` |
| B2 | PASS | Full bounded no-DUT rest lifecycle; carried from v2 because the v3 change does not affect this path |
| B3 | PASS | Run `14cebce0-d591-4ce8-8898-e433d32061bf`; stopped in 3.766 s with no fallback |
| B4 | PASS | Initiating-client loss did not affect service ownership; carried from v2 because the v3 change does not affect this path |
| B5 | PASS | Run `d1dd78fd-d334-41ff-acb2-31ae4a19c5b8`; shutdown stop converged in approximately 3.410 s with no fallback; recovery preflight passed |

The former v2 B3 result remains historical `FAIL`: 3.390 s exceeded its former
3.0 s policy and requested fallback. It was corrected by the explicitly approved
5.0 s v3 policy, not rewritten as a pass.

## Final state and cleanup

- Operator confirmed output OFF, watchdog OFF, channels disabled, setpoints
  zero, no fault and no physical anomaly.
- Service and monitor are stopped; ports 9300 and 9400 have no listener.
- Approved v1/v2 profiles and all physical reports remain preserved as evidence.
- Temporary failed pytest directory and campaign Python cache were removed.
- `.test-temp-final-2` remains due Windows/OneDrive access denial.

## Next gate

Tier N does not authorize energization. Tier E must begin at C0 with the battery
module, fixture, limits, profile and stop procedure explicitly reviewed and
approved. Each energizing run requires its own immediate authorization.
