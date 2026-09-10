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
