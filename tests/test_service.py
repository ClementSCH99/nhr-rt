from __future__ import annotations

import threading

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
        assert client.measurement("sim-1")["instrument_id"] == "sim-1"
        disabled = client.command("sim-1", "disable")
        assert disabled["enabled"] is False
        assert client.disconnect("sim-1") == {"connected": False}
    finally:
        server.shutdown()
        thread.join()
        manager.close()
        server.server_close()
