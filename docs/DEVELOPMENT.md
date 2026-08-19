# Development guide

## Environment

Use Python 3.12. Hardware and IVI tests require the repository's 32-bit
environment. Unit tests can run in any compatible Python architecture.

```powershell
.\.venv32\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp="$env:TEMP\nhr-rt-tests"
```

OneDrive may lock pytest temporary directories after successful tests. Use a
fresh ASCII path under the system temporary directory and report cleanup errors
separately from functional failures.

## Change rules

- Keep all backend calls behind `NHR9300`; COM objects never cross its worker
  thread.
- Add safety behavior first to the simulator and tests, then validate on
  hardware through an approved profile.
- Preserve HTTP/client compatibility or provide an explicit migration and
  contract test.
- A sink or interlock adapter must not weaken timing, freshness or fail-closed
  behavior.
- Never commit an approved hardware profile under `examples`.
- Generated reports, local profiles, environments and test outputs stay
  ignored. Accepted evidence is indexed by hash and published as release assets.

## Validation levels

1. Unit and simulator tests.
2. Service/client integration with separate 32-bit and 64-bit processes.
3. Exact-profile simulation and non-energizing preflight.
4. One supervised hardware execution.
5. Independent safe-state verification and evidence archive.
