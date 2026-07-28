from __future__ import annotations

import threading
import time
from pathlib import Path

from nhr9300.client import NHRServiceClient
from nhr9300.service import build_server


def test_service_and_64_bit_compatible_client(tmp_path) -> None:
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [{"id": "sim-1", "backend": "simulator"}],
        },
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    client = NHRServiceClient(f"http://{host}:{port}")
    try:
        assert client.instruments() == [
            {"instrument_id": "sim-1", "connected": False}
        ]
        connected = client.connect("sim-1")
        assert connected["connected"] is True
        deadline = time.monotonic() + 1
        while client.acquisition("sim-1")["sample_count"] == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        acquisition = client.acquisition("sim-1")
        assert acquisition["requested_rate_hz"] == 5
        assert acquisition["active"] is True
        assert acquisition["sample_count"] >= 1
        assert acquisition["first_sample_at"] is not None
        assert acquisition["csv_path"].endswith(".csv")
        assert acquisition["last_error"] is None
        assert client.measurement("sim-1")["instrument_id"] == "sim-1"
        disabled = client.command("sim-1", "disable")
        assert disabled["enabled"] is False
        assert client.disconnect("sim-1") == {"connected": False}
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()


def test_startup_summary_and_effective_configuration(tmp_path, capsys) -> None:
    config_path = Path(tmp_path) / "service.json"
    server, manager = build_server(
        {
            "output_dir": str(tmp_path),
            "instruments": [
                {"id": "sim-start", "backend": "simulator", "rate_hz": 10}
            ],
        },
        port=0,
        config_path=config_path,
    )
    try:
        output = capsys.readouterr().out
        configuration = manager.configuration()
        instrument = configuration["instruments"][0]

        assert str(config_path) in output
        assert "sim-start backend=simulator rate=10 Hz" in output
        assert instrument["requested_rate_hz"] == 10
        assert instrument["csv_path"] in output
        assert configuration["listen_url"] in output
        assert configuration["restart_required_for_config_changes"] is True
    finally:
        manager.close()
        server.server_close()
