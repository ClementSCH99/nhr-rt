"""Reference 64-bit producer for the public external-snapshot contract."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from nhr9300 import (
    ExternalSnapshotPublisher,
    NHRError,
    NHRServiceClient,
    NHRTransportError,
)


def load_records(path: Path) -> Iterator[dict[str, Any]]:
    """Read health/signals records from a small JSON Lines fixture."""
    with path.open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            record = json.loads(raw_line)
            if not isinstance(record, dict):
                raise ValueError(f"Line {line_number} must be a JSON object")
            unknown = set(record) - {"timestamp_utc", "health", "signals"}
            if unknown or "health" not in record or "signals" not in record:
                raise ValueError(f"Line {line_number} has invalid fields")
            if not isinstance(record["health"], str) or not isinstance(
                record["signals"], Mapping
            ):
                raise ValueError(f"Line {line_number} has invalid health/signals")
            yield record


def require_external_snapshot_contract(configuration: Mapping[str, Any]) -> None:
    contracts = configuration.get("contracts", {})
    capabilities = configuration.get("capabilities", [])
    if (
        not isinstance(contracts, Mapping)
        or contracts.get("external_snapshot") != "1.0"
        or not isinstance(capabilities, list)
        or "external_snapshot_publication" not in capabilities
    ):
        raise RuntimeError("NHR service does not expose external snapshot contract 1.0")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON Lines health/signals file")
    parser.add_argument("--service-url", default="http://127.0.0.1:9300")
    parser.add_argument("--instrument-id", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--timeout-s", type=float, default=1.0)
    parser.add_argument("--retry-delay-s", type=float, default=0.5)
    args = parser.parse_args(argv)
    if args.retry_delay_s < 0:
        parser.error("--retry-delay-s must be non-negative")

    client = NHRServiceClient(args.service_url)
    try:
        require_external_snapshot_contract(client.configuration())
        publisher = ExternalSnapshotPublisher(
            client,
            args.instrument_id,
            args.source_id,
            timeout_s=args.timeout_s,
        )
        for record in load_records(args.input):
            timestamp_utc = record.get("timestamp_utc") or datetime.now(
                timezone.utc
            ).isoformat()
            try:
                receipt = publisher.publish(
                    timestamp_utc=timestamp_utc,
                    health=record["health"],
                    signals=record["signals"],
                )
            except NHRTransportError:
                time.sleep(args.retry_delay_s)
                receipt = publisher.retry_pending()
            print(json.dumps(receipt, sort_keys=True))
    except (NHRError, OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"External snapshot producer failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
