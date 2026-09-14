# Non-approved workflow library

Every template in this directory is **NON APPROUVÉ / UNAPPROVED**. No entry is
automatically registered. Values are illustrative and are not DUT ratings or
permission to energize. Copy a template into your reviewed local profile area.

| Template | Use case / stop condition | Specific review points |
|---|---|---|
| `cc.example.json` | Constant current until voltage condition, bounded by timeout | Direction, A, V boundary, W ceiling, condition polarity |
| `cccv.example.json` | CCCV charge; current cutoff after voltage activation | CC current, CV voltage, cutoff current, timeout, power channel policy |
| `constant_power.example.json` | Constant power over reviewed duration | W request, A and V constraints, direction |
| `rest.example.json` | Disabled-output rest and observation for duration | Duration and applicability of the initial/final safe-state procedure |
| `sequence.example.json` | CCCV, rest, then CP discharge | Each stage plus maximum sequence duration |
| `dynamic.example.json` | Signed current/power CSV profiles and rest | CSV time seconds, signed A/W values, direction changes, voltage boundaries |
| `cc_interlocks.example.json` | CC with isolation permissive and temperature maximum | BMS signal mapping, bool/degC units, maximum temperature and freshness seconds |

For every copy review both safety and workflow limits, exact resource/serial,
bench description, independent safe-state/stop procedure, watchdog, durations,
all channel-enable choices, termination behavior and source freshness. Keep
`approved: false` until that review is complete. A digest establishes identity,
not approval. The bundle tool deliberately rejects unapproved examples.

The dynamic files are in `../profiles/`; bundle identity covers their bytes too.
The interlock example requires a service-owned external snapshot producer and
will refuse execution with missing inputs. Neither its 40 degC limit nor its
1 s freshness allowance is a qualified value.

For a runnable **software-only** console exercise, use
`../operator-simulation/service.json`. Its approvals apply only to its simulator
fixture, which is separate from this library. Never switch that config to IVI.
