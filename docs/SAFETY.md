# Safety contract

Software controls complement physical protection; they do not replace the NHR
emergency stop, fixture protection or an operator-approved test plan.

## Invariants

- Hardware workflows require an approved profile, exact resource and serial
  match, explicit acknowledgement, initially disabled output and initially
  disabled watchdog.
- Approved safety limits are programmed and read back before arm or setpoints.
- Active writes require a live arm lease, a fresh measurement and safe/fresh
  interlocks.
- An external interlock failure during an approved workflow requests a
  controlled stop first. The approved timeout escalates to `emergency_stop` if
  the workflow does not become terminal. Other acquisition/safety failures
  retain the immediate emergency-stop response when the module may be energized.
- Normal completion, failure and interruption converge on zeroed channels,
  disabled output and disabled watchdog, followed by an independent reconnect.
- Only the 32-bit service owns a physical NHR in the supported architecture.

## Long static stages

CC, CCCV and CP stages remain limited to 295 seconds unless the approved
`workflow_limits` explicitly contains `"arm_lease_renewal_enabled": true`.
Renewal is available only through a registered service-owned workflow; the
standalone runner rejects it. The service fixes each lease at no more than 300
seconds and renews with a 60-second margin. Renewable stages are capped at
28,800 seconds (8 hours), while complete sequences are capped at 43,200 seconds
(12 hours). Each approved workflow should retain the lower limits appropriate
to its actual test plan.

Every renewal requires active service-owned acquisition, a fresh NHR
measurement, watchdog readback ON, safe/fresh interlocks, unchanged approved
safety limits and unchanged stage setpoints. Refusal requests controlled stop;
the approved timeout escalates to emergency stop. Actual lease expiration uses
emergency stop immediately. Renewal never changes a stage or sequence deadline.

Runtime, `run-state.json`, `report.json` and `artifacts.json` retain the renewal
decisions and stop cause. SSE only presents the runtime state and is never part
of the renewal decision.

## Dynamic zero behavior

A zero point inside a signed CSV keeps the current charge/discharge state and
programs 0 A or 0 W. This avoids relay cycling during dynamic profiles but does
not guarantee electrical isolation. Bench validation measured residuals up to
approximately +0.47 A / +42 W. Use a `rest` stage when isolation or a true zero
is required.

Charge-to-discharge transitions are direct active-state changes. The validated
NHR did not cycle its contactors during the transition. A final `rest`, cleanup
or emergency stop still disables the output.

## External interlocks

External adapters must timestamp the source measurement, not the time it was
forwarded. They must return unsafe when transport health, decoding, required
signals or freshness cannot be established. Combining providers never turns an
unsafe result into a safe one.

Approved workflow rules are typed and covered by the immutable bundle digest.
Pre-start failures reject start; runtime failures latch until workflow end and
cannot automatically restart or reset. See
[External fail-closed interlocks](EXTERNAL_INTERLOCKS.md).

UUT temperature is not considered validated unless a real sensor is wired and
included in the approved interlock/profile contract.
