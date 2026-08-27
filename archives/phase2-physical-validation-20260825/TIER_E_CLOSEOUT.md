# Phase 2 Tier E Closeout

> Archived campaign record. Referenced raw files and approved profiles were
> removed during closeout; see [archive notes](README.md).

**Campaign:** `PHASE2-PHYSICAL-20260825-NHR79503`

**Instrument:** `DC PM 1`, serial `79503`

**Execution dates:** 2026-08-26 to 2026-08-27

**Final disposition:** `PASS` for the exact approved Tier E scope

## Approved physical contract

- DUT: NMC module, 24s2p, 66 Ah, approximately 55% SOC at campaign start.
- Approved test window: 80--100 V, 10 A maximum in either direction and
  1000 W maximum in either direction.
- Tier E profile: 5 A constant-current charge, 89.5 V and 500 W profile
  ceilings, 60 s charge followed by 10 s `rest`.
- Active workflow: `phase2-tier-e-base-cc-5a-60s-v3`.
- Bundle digest:
  `sha256:eb7c64fc2e6fdf5ec83f5b76232edf84d3074a49be79c5243f8303f9295cb81c`.
- Controlled-stop/watchdog acceptance bound: 10.0 s.
- External temperature monitoring with physical stop at 50 degC; observed
  campaign temperature remained 25 degC.
- BMS monitoring only, no contactor and no fuse. Cables/connectors, manual
  breakers, polarity and emergency-stop access were explicitly approved by the
  operator for this campaign.

The v2 and v3 Tier E profiles have identical electrical stages and limits. The
v3 revision changes only the workflow identity and the controlled-stop bound
from 5.0 s to 10.0 s.

## Test matrix

| Test | Final result | Principal evidence |
|---|---|---|
| C0 - Module/profile approval | PASS | Operator-approved DUT, limits, fixture and emergency contract |
| C1 - Short approved workflow | PASS | Run `de6c857a-8c87-4f87-a9d6-cae1dcab780e`; full 60 s + 10 s profile; 0.08324 Ah / 7.397 Wh; verified final safe state |
| C2 - Simultaneous monitor observation | PASS | Run `5fc6c907-d6fd-4394-bde7-62e14023d7bb`; monitor/observers did not interfere; full profile passed; acquisition near 10 Hz |
| C3 - Controlled stop under energy | PASS | v3 run `073c6c81-bada-4d61-b834-61cab30866eb`; terminal safe state in 8.203 s, below 10.0 s; no fallback |
| C4 - Client loss under energy | PASS | Run `a368bc97-b946-459d-b200-121dc29155e7`; initiating client lost near 5 s; same service-owned run completed normally; 0.08321 Ah / 7.399 Wh |
| C5 - Graceful service shutdown under energy | PASS | Run `9653ab08-f4b5-443e-af67-06396a374634`; cooperative stop in 7.853 s and full service shutdown in 8.301 s; no fallback |
| C6 - Abrupt service loss/watchdog | PASS | Run `8210055c-06fd-4ba0-b787-1acc984c7393`; forced loss of only the verified service PID; watchdog drove physical output OFF in less than approximately 10 s; run recovered as `interrupted`; recovery preflight passed |
| Z1 - Campaign closeout | PASS | Final service/monitor snapshots safe and concordant; monitor/service stopped normally; no process or listener on 9300/9400; post-shutdown physical safe state confirmed |

## Retained deviations and interpretation

- The two v2 C3 attempts remain historical `FAIL` results against their former
  5.0 s bound: runs `6cd5bf19-1145-40d1-b998-1df1309aed54` and
  `15e19ff9-c39e-49c1-a225-0b9c2df414b4` converged in 8.109 s and 8.078 s and
  requested fallback. They are not rewritten as passes.
- C3 was requalified only after explicit operator approval of the 10.0 s bound
  and immutable v3 workflow. The successful v3 result is 8.203 s without
  fallback.
- C6 watchdog timing is an operator-observed bound (`<10 s`), not a
  high-resolution physical timestamp. The NHR displayed `Watchdog service
  timeout`; no emergency stop or breaker was used.
- Abrupt C6 loss intentionally produced no completed workflow report. Its
  evidence is the pre-loss snapshot, persistent `interrupted` manifest, direct
  recovery record and recovery-preflight report.
- The first direct C6 recovery attempt stopped at a software guard before any
  hardware write because approved limits were not installed in that temporary
  facade. Readback already showed a complete safe state. The helper was
  corrected to avoid unnecessary writes; its repeat verified output/watchdog
  OFF and zeroed/disabled channels.
- The NHR UUT-temperature channel is unwired and excluded by the approved
  contract. Temperature acceptance relies only on the operator's external
  observation.

## Evidence index and hashes

| Evidence | SHA-256 |
|---|---|
| `runs/workflow-runs/de6c857a-8c87-4f87-a9d6-cae1dcab780e/report.json` | `7A999B17A9A7E118643A4B9C1717D3315E43A26A756C527C792C30769E9DB6B2` |
| `runs/workflow-runs/5fc6c907-d6fd-4394-bde7-62e14023d7bb/report.json` | `320FE9CB90C106CAAC6925F31DB20F33E25F4F11C0C5E0342F96747699914A16` |
| `runs/workflow-runs/073c6c81-bada-4d61-b834-61cab30866eb/report.json` | `C3A42F5E12A8EDA6249AC72A4D995F615D79774499E902A47E88533D9965E770` |
| `runs/workflow-runs/a368bc97-b946-459d-b200-121dc29155e7/report.json` | `F866035028BAE2EF0BE015E5B90FE58E822BE4F9550C1F364365F31E55B7322D` |
| `runs/workflow-runs/9653ab08-f4b5-443e-af67-06396a374634/report.json` | `4773417F2EF798C06F0D97837400CEFACA37E9E849640560FB7E09D1671DB5B8` |
| `runs/workflow-runs/8210055c-06fd-4ba0-b787-1acc984c7393/run-state.json` | `781FCE079BE41C202B88692DADE235A2353741A0D237B2E2B08954D34231DF67` |
| `runs/PHASE2-PHYSICAL-20260825-NHR79503/tier_e_c6_pre_loss_8210055c-06fd-4ba0-b787-1acc984c7393.json` | `7EB08A5FBB46481E39BBC777BACA6A4B8982513A8925DD1110D1CE22662D0788` |
| `runs/PHASE2-PHYSICAL-20260825-NHR79503/tier_e_c6_direct_recovery.json` | `DC7DCB5A3BAEC407BB26B6C6C67AE44F9E181CDB44D327A8A673084A16603223` |
| `runs/workflow-preflights/preflight-20260827T140234-3bf23e4d/report.json` | `E6E958AF0A94165083AE8DBD7D8C388ABB0A5B361A78EF83605DA79C82AF44BA` |

The detailed ordered chronology and physical operator observations remain in
`runs/PHASE2-PHYSICAL-20260825-NHR79503/validation_log.md`.

## Final safe state and readiness boundary

Before process shutdown, the service and independent monitor agreed on
workflow idle, output OFF, all channels disabled, all V/I/P setpoints zero,
fresh measurement near 88.81 V, healthy acquisition near 10 Hz and no alerts.
Immediately before process shutdown, the operator independently confirmed
output/watchdog OFF, NHR state `OFF`, fault cleared, external temperature
25 degC and no anomaly.

The monitor and service then stopped normally. No `nhr9300-service` or
`nhr9300-monitor` process remains, ports 9300 and 9400 have no listener and both
local endpoints are offline.

Tier E is accepted for this exact module, charge-only profile, limits, service
configuration and supervised workflow. This does not validate discharge,
another DUT/profile, external CAN/BMS fail-closed interlocks, dynamic SoP,
unattended operation, fixture protection beyond the operator-approved setup or
general product release. Any such expansion requires a new reviewed contract
and physical authorization. No Git commit, tag or push was performed.

After the completed monitor/service shutdown, the operator independently
confirmed output/watchdog OFF, channels disabled, V/I/P setpoints zero, NHR
state `OFF`, no displayed fault, external temperature 25 degC and no anomaly.
The DUT remains connected intentionally in this verified safe state. Z1 final
disposition is `PASS`.
