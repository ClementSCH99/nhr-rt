# Non-approved workflow library

Every template in this directory is **NON APPROUVÉ / UNAPPROVED**. No entry is
automatically registered. Values are illustrative and are not DUT ratings or
permission to energize. Copy a template into your reviewed local profile area.

| Template | Use case / stop condition | Specific review points |
|---|---|---|
| `cc.example.json` | Constant current until voltage condition, bounded by timeout | Direction, A, V boundary, W ceiling, condition polarity |
| `cccv.example.json` | CCCV charge; current cutoff after voltage activation | CC current, CV voltage, cutoff current, timeout, power channel policy |
| `constant_power.example.json` | Constant power until its voltage condition, bounded by timeout | W request, A and V constraints, direction and condition polarity |
| `rest.example.json` | Disabled-output rest and observation for duration | Duration and applicability of the initial/final safe-state procedure |
| `sequence.example.json` | CCCV, rest, CP discharge and optional final relaxation | Each stage, post-sequence rest and maximum sequence duration |
| `dynamic.example.json` | Signed current/power CSV profiles and rest | CSV time seconds, signed A/W values, direction changes, voltage boundaries |
| `cc_interlocks.example.json` | CC with critical isolation and temperature interlocks | BMS signal mapping, bool/degC units, extreme temperature and freshness seconds |
| `discharge_terminations.example.json` | CC discharge ending on the first of capacity, BMS minimum cell voltage or BMS maximum cell temperature; then measured rest | Condition order, normal versus critical thresholds, source freshness, rest duration and total duration |

For every copy review both safety and workflow limits, exact resource/serial,
bench description, independent safe-state/stop procedure, watchdog, durations,
all channel-enable choices, termination behavior and source freshness. Keep
`approved: false` until that review is complete. A digest establishes identity,
not approval. The bundle tool deliberately rejects unapproved examples.

The dynamic files are in `../profiles/`; bundle identity covers their bytes too.
The interlock and discharge-termination examples require a service-owned
external snapshot producer and will refuse execution with missing inputs.
`cc_interlocks.example.json` illustrates critical rules only. In
`discharge_terminations.example.json`, `termination_conditions` end the active
stage normally; the first condition reached is recorded in the report and the
10 s `rest` records relaxation with output disabled. Critical interlocks remain
active in both stages. A termination on an external signal requires a runtime
interlock on that same source, signal and unit with a strictly more extreme
threshold. No threshold or freshness period in these templates is qualified
for a real DUT.

For a runnable **software-only** console exercise, use
`../operator-simulation/service.json`. Its approvals apply only to its simulator
fixture, which is separate from this library. Never switch that config to IVI.
