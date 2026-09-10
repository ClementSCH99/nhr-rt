# Runtime observability contract

This document freezes the Milestone 3 read-only contract for v0.3.0. The
versioned surfaces are:

- `GET /api/v1/instruments/{id}/runtime` for one consolidated snapshot;
- `GET /api/v1/instruments/{id}/events` for bounded Server-Sent Events (SSE).

These endpoints do not connect, arm, configure, start, stop or otherwise own
the instrument. `NHR9300` remains the safety authority, the acquisition
collector owns measurements and evidence, and the workflow controller owns run
state. Observability only copies and assembles their public/cached state.

## Runtime snapshot schema 1.0

Every snapshot contains:

| Object | Contract |
|---|---|
| `instrument` | Connection, remote mode, operating state, output status and setpoints |
| `measurement` | Latest sample, availability, monotonic age, configured maximum age and freshness result |
| `acquisition` | Active/health state, requested and observed rate, sample count, absolute evidence path and error |
| `workflow` | Active run identity/state, stage, step, termination, report path, totals and progress when available |
| `totals` | Latest NHR charge/discharge Ah and Wh counters; unavailable counters are `null` |
| `external_sources` | M5 source health, sequence, UTC/receive timestamps, age, active phase, rule results and latch state |
| `interlocks` | Current provider results with safe/fresh decisions, monotonic age and allowed age |
| `effective_power_limits` | Approved static power limits when configured; external contribution remains `not_configured` until Milestone 6 |
| `alerts` | Active acquisition and interlock faults known at snapshot time |

`generated_at_utc` is for cross-process correlation. Measurement and interlock
freshness use only the service monotonic clock.

Progress is deliberately nullable. A duration-bounded stage reports elapsed
seconds, its maximum duration and a percentage of that reviewed time bound.
When a termination condition exists, `termination_metric` separately reports
its current metric and target with `percent: null`; it does not turn a threshold
into a guessed completion percentage. Idle or
preflighting workflows report `progress_available: false`; clients must not
invent a percentage.

The snapshot is coherent at the service ownership boundary, not an atomic
hardware sample. Status, latest measurement, acquisition state and workflow
state are collected under the managed-instrument lifecycle lock, but they may
have distinct source timestamps and cadences.

## Event schema 1.0

The SSE `event` name is one of:

- `measurement`;
- `workflow`;
- `interlock`;
- `limit`;
- `alert`.

Each `data` value is a JSON envelope with `schema_version`, a service-local
monotonically increasing `sequence`, `timestamp_utc`, `instrument_id`, `event`
and the event-specific `data` object. The SSE `id` equals `sequence`.

Each viewer owns a bounded queue (100 events by default, configurable with
`event_queue_size`). Publication never waits for a viewer. When a queue is
full, its oldest event is discarded; the next delivered envelope includes
`dropped_before`. Consumers must refresh `/runtime` after a gap. The stream is
for live display, not durable safety or test evidence.

Normal EOF, browser close and connection reset are informational events and do
not stop acquisition or a workflow. Unexpected handler/transport errors and
acquisition failures remain logged; acquisition failures also appear in the
runtime alert set. Service shutdown closes the event broker so handlers can
leave their bounded queue wait.

## External interlocks and reserved work

Milestone 5 gives `external_sources` and external `interlocks` safety meaning.
When no workflow rules are active, source status is `inactive`; this is not a
safe decision. During pre-start/runtime, the snapshot reports `configured`, the
active phase and latched failures. See
[EXTERNAL_INTERLOCKS.md](EXTERNAL_INTERLOCKS.md).

All SSE `interlock` events share one payload: `instrument_id`,
`external_sources` and `results`. A latched external rule result carries the
exact triggering `source_sequence`, `source_timestamp_utc`,
`source_received_at_utc` and `source_age_s_at_evaluation`; the live source list
may legitimately show a newer snapshot.

The dynamic SoP envelope remains reserved for Milestone 6, so its external
contribution continues to report `not_configured`.
