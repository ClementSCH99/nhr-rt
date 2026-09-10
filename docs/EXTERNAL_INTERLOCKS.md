# External fail-closed interlocks

Milestone 5 lets the 32-bit `nhr-rt` service make pre-start and runtime safety
decisions from values decoded by another local process such as CAN-PY. CAN-PY
owns CAN acquisition and decoding; it never receives NHR hardware authority.

## Snapshot contract

Publish one complete source snapshot with the typed client:

```python
from datetime import datetime, timezone

from nhr9300 import NHRServiceClient

client = NHRServiceClient("http://127.0.0.1:9300")
client.submit_external_snapshot(
    "nhr-79503",
    "bms-main",
    sequence=1842,
    timestamp_utc=datetime.now(timezone.utc).isoformat(),
    health="ok",
    signals={
        "pack_voltage_v": 87.4,
        "max_cell_temperature_c": 41.2,
        "hv_permissive": True,
    },
)
```

The HTTP form is
`PUT /api/v1/instruments/{id}/external-sources/{source_id}/snapshot` with
exactly `sequence`, `timestamp_utc`, `health` and `signals` in the JSON body.
The source ID is 1–64 letters, digits, dots, underscores or hyphens. Sequence
numbers are non-negative integers and must strictly increase per source.
Timestamps require an ISO-8601 UTC offset and must describe the source data,
not the forwarding time. Signal values are finite JSON numbers or booleans.
The service accepts at most 32 tracked external sources, 256 signals per
snapshot and 262144 bytes per JSON request body. These bounds protect the
service from an accidental unbounded publisher; they are not an authentication
mechanism.

If the publisher does not receive the HTTP response, it must retry the exact
same sequence, timestamp, health and signals. That retry is idempotent. Reusing
the sequence with different data, or submitting an older sequence, is rejected
and fails the source closed. A publisher that restarts while the service stays
running must read `client.interlocks(instrument_id)`, find its last accepted
sequence under `external_sources.sources`, and continue above it. A service
restart clears this in-memory sequence history; continuing with the publisher's
existing increasing sequence remains valid. Only one publisher may own a given
`source_id`.

Only `health: "ok"` is safe. Missing fields, invalid values, unhealthy source
state, a timestamp more than one second in the future, an older sequence or a
reused sequence with different data records a source rejection. A later valid
snapshot with a strictly greater sequence is required to clear that source-
level rejection; replaying an earlier accepted snapshot does not clear it.

The service derives initial source age from UTC and then advances age with its
own monotonic clock. Cross-process monotonic clocks are never compared.

## Approved workflow rules

Rules live in `workflow.json`, so the existing bundle digest covers every
threshold and freshness value:

```json
"external_interlocks": [
  {
    "rule_id": "pack_voltage_window",
    "source_id": "bms-main",
    "signal": "pack_voltage_v",
    "comparison": "range",
    "minimum": 72.0,
    "maximum": 100.8,
    "unit": "V",
    "applies": "both",
    "max_age_s": 0.5,
    "stability_duration_s": 0.2
  },
  {
    "rule_id": "bms_hv_permissive",
    "source_id": "bms-main",
    "signal": "hv_permissive",
    "comparison": "equals",
    "expected": true,
    "unit": "boolean",
    "applies": "both",
    "max_age_s": 0.5
  }
]
```

`comparison` is `minimum`, `maximum`, `range`, or boolean `equals`.
Comparisons are inclusive: minimum means `value >= minimum`, maximum means
`value <= maximum`, and range includes both bounds. `applies` is `pre_start`,
`runtime`, or `both`. `max_age_s` is required, finite and positive.

Optional `stability_duration_s` delays assertion of a safe permissive; it does
not debounce an unsafe runtime value. Runtime violations latch for the rest of
the workflow even if a newer snapshot returns to a safe value. A subsequent
workflow starts with a new latch but must pass its current pre-start rules.

## Stop behavior and evidence

A missing, unhealthy, stale or unsafe pre-start input rejects the start before
a run is reserved. During an active workflow, the acquisition safety loop
detects the external failure, records the rule/source/value, requests the same
cooperative stop used by an operator, and begins the locally approved
controlled-stop timeout. Normal cleanup targets standby, disabled output,
disabled watchdog and zeroed channels. If the workflow thread is still alive
at the timeout, the service requests `emergency_stop`.

Use `client.interlocks(instrument_id)`, `client.runtime(instrument_id)` and the
runtime SSE `interlock` events for live observation. Every `interlock` event
uses the same payload keys: `instrument_id`, `external_sources` and `results`.
The terminal run snapshot and `report.json` preserve the stop origin and, for
each latched result, the exact source sequence, source/receive timestamps and
age at evaluation, plus whether emergency fallback was requested. SSE remains
live observability, not durable evidence.

## Safety boundary

The snapshot endpoint can update only decoded external state. It cannot arm,
set limits, change setpoints or stop/start a workflow directly. Localhost is
the deployment boundary; the service has no network authentication and must
not be exposed on another interface.

Software/simulator tests do not validate the real CAN path, DBC identity,
units, source cadence, sensor placement, wiring, physical stop time or battery
thresholds. These require a separately reviewed integration and supervised
hardware-validation plan. An unwired NHR UUT-temperature channel is not an
acceptable temperature interlock.
