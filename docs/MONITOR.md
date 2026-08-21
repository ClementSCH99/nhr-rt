# Read-only web monitor

`nhr9300-monitor` is the Milestone 4 local operator display. It runs in a
standard 64-bit Python environment, contains no IVI dependency and serves all
HTML, CSS and JavaScript assets from the installed `nhr9300` package. It never
requires internet access.

## Authority boundary

The monitor creates one public `NHRServiceClient` and calls only
`runtime(instrument_id)`. Its internal reader protocol deliberately exposes no
other client method. The browser can request only:

- `GET /` and the packaged assets;
- `GET /api/config` for display cadence and instrument identity;
- `GET /api/runtime` for the unmodified runtime snapshot.

`HEAD` is supported for diagnostics. `POST`, `PUT`, `PATCH`, `DELETE` and
`OPTIONS` return HTTP 405. There is no monitor route for connect, disconnect,
preflight, start, stop, setpoints or emergency stop. Opening or closing the
page therefore does not own or alter the NHR connection.

The monitor binds only to localhost. Like the NHR service, it is not a network
authentication boundary and must not be exposed on another interface.

## Run in 64-bit Python

Install the base package in a normal 64-bit Python 3.12 environment, then keep
the 32-bit service running separately:

```powershell
python -m pip install -e .
nhr9300-monitor --instrument-id nhr-79503
```

The default service URL is `http://127.0.0.1:9300`; the monitor page is served
at `http://127.0.0.1:9400`. Override them when needed:

```powershell
nhr9300-monitor `
  --service-url http://127.0.0.1:9300 `
  --instrument-id nhr-79503 `
  --port 9400
```

The page refreshes sequentially at 1 Hz. It keeps at most 600 distinct
measurement samples in browser memory for the rolling V/I/P trends. That
display buffer is not durable evidence; the acquisition CSV remains the
evidence source.

## Display interpretation

- **Service link** is the age of the last successful monitor-to-service
  snapshot. It is not an external safety heartbeat.
- **Measurement freshness** comes directly from the M3 runtime contract and
  uses the service monotonic clock.
- **Workflow progress** is shown only when the API supplies a determinate
  percentage. A termination threshold is never converted into a guessed
  percentage.
- **External / SoP** remains `not_configured` until Milestones 5 and 6 give
  those fields validated safety meaning.
- **Read only** describes monitor authority. PowerPanel remains available for
  vendor diagnostics and independent checks.

## Validation boundary

Software fixtures and the simulator can verify rendering inputs, offline
assets, error/stale states and absence of monitor-side state changes. They do
not validate IVI timing, physical behavior, external heartbeat loss, dynamic
SoP limits or simultaneous monitoring during an energized test.
