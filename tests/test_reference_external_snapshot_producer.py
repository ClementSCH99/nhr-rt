from __future__ import annotations

import json
import os
import runpy
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from nhr9300 import ExternalSnapshotPublisher, NHRServiceClient
from nhr9300.service import build_server


ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "external_snapshot_producer.py"
FIXTURE = ROOT / "examples" / "external_snapshots.example.jsonl"


def test_reference_producer_publishes_fixture_through_public_api(
    tmp_path, capsys
) -> None:
    module = runpy.run_path(str(EXAMPLE))
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-reference", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        result = module["main"](
            [
                str(FIXTURE),
                "--service-url",
                f"http://{host}:{port}",
                "--instrument-id",
                "sim-reference",
                "--source-id",
                "bms-reference",
            ]
        )
        assert result == 0, capsys.readouterr().err
        status = NHRServiceClient(f"http://{host}:{port}").interlocks(
            "sim-reference"
        )
        source = status["external_sources"]["sources"][0]
        assert source["source_id"] == "bms-reference"
        assert source["sequence"] == 1
        assert source["health"] == "ok"

        restarted = ExternalSnapshotPublisher(
            NHRServiceClient(f"http://{host}:{port}"),
            "sim-reference",
            "bms-reference",
        )
        receipt = restarted.publish(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            health="ok",
            signals={"pack_voltage_v": 90.2},
        )
        assert receipt["sequence"] == 2
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()


def test_external_snapshot_publisher_runs_from_separate_64_bit_process(
    tmp_path,
) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-64", "backend": "simulator"}],
        },
        port=0,
        announce=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    code = """
import json
import struct
import sys
from datetime import datetime, timezone
from nhr9300 import ExternalSnapshotPublisher, NHRServiceClient

assert struct.calcsize('P') * 8 == 64
assert 'comtypes' not in sys.modules
client = NHRServiceClient(sys.argv[1])
configuration = client.configuration()
assert configuration['contracts']['external_snapshot'] == '1.0'
publisher = ExternalSnapshotPublisher(client, 'sim-64', 'bms-64')
receipt = publisher.publish(
    timestamp_utc=datetime.now(timezone.utc).isoformat(),
    health='ok',
    signals={'pack_voltage_v': 90.0, 'hv_permissive': True},
)
print(json.dumps({'bits': 64, 'sequence': receipt['sequence']}))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str((ROOT / "src").resolve())
    try:
        completed = subprocess.run(
            ["py", "-3.12", "-c", code, f"http://{host}:{port}"],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        result = json.loads(completed.stdout)
        assert result == {"bits": 64, "sequence": 0}
        status = NHRServiceClient(f"http://{host}:{port}").interlocks("sim-64")
        assert status["external_sources"]["sources"][0]["source_id"] == "bms-64"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()
