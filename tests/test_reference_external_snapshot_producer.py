from __future__ import annotations

import runpy
import threading
from pathlib import Path

from nhr9300 import NHRServiceClient
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
    finally:
        server.shutdown()
        thread.join(timeout=2)
        manager.close()
        server.server_close()
