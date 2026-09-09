"""Non-energizing command-line diagnostics and workflow bundle inspection."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import socket
import struct
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .evidence import check_output_directory
from .workflow_registry import WorkflowBundle, WorkflowRegistry


def _load_config(path: Path) -> dict[str, Any]:
    """Load a JSON service configuration without creating an instrument."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Service configuration root must be an object")
    return value


def diagnose_config(path: Path) -> dict[str, Any]:
    """Validate paths, bundles and evidence storage without touching hardware."""
    resolved = path.resolve()
    config = _load_config(resolved)
    output_dir = Path(str(config.get("output_dir", "runs")))
    instruments = config.get("instruments", [])
    backends = {str(item["id"]): str(item.get("backend", "ivi")) for item in instruments}
    registry = WorkflowRegistry.from_config(
        config, base_dir=resolved.parent, instrument_backends=backends
    )
    listen_url = str(config.get("listen_url", "http://127.0.0.1:9300"))
    parsed = urlparse(listen_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9300
    service_reachable = False
    try:
        with socket.create_connection((host, port), timeout=0.2):
            service_reachable = True
    except OSError:
        pass
    try:
        version = importlib.metadata.version("nhr9300")
    except importlib.metadata.PackageNotFoundError:
        version = "source-tree"
    return {
        "safe_scope": "software_and_read_only_no_instrument_connection",
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "bitness": struct.calcsize("P") * 8,
        },
        "package_version": version,
        "ivi_dependency_available": importlib.util.find_spec("comtypes") is not None,
        "config_file": str(resolved),
        "output_dir": check_output_directory(output_dir),
        "listen": {"host": host, "port": port, "service_reachable": service_reachable},
        "instruments": [
            {
                "instrument_id": str(item["id"]),
                "backend": str(item.get("backend", "ivi")),
                "operator_supervised": bool(item.get("operator_supervised", False)),
                "remote_workflow_control": bool(item.get("remote_workflow_control", False)),
                "primitive_compatibility_control": bool(
                    item.get("primitive_compatibility_control", False)
                ),
            }
            for item in instruments
        ],
        "workflows": [item for instrument_id in backends for item in registry.list_for(instrument_id)],
    }


def doctor_main() -> int:
    """Entry point for the non-energizing ``nhr9300-doctor`` command."""
    parser = argparse.ArgumentParser(
        description="Inspect an NHR service setup without connecting hardware"
    )
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(diagnose_config(args.config), indent=2))
    return 0


def _bundle_description(bundle: WorkflowBundle) -> Mapping[str, Any]:
    return {
        "profile_path": str(bundle.profile_path),
        "bundle_digest": bundle.digest,
        "expected_resource": bundle.configuration.expected_resource,
        "expected_serial_number": bundle.configuration.expected_serial_number,
        "simulation_initial_voltage_v": bundle.configuration.simulation_initial_voltage_v,
        "workflow_approved": bundle.configuration.workflow_limits.approved,
        "safety_limits_approved": bundle.configuration.safety_limits.approved,
        "files": [
            {
                "logical_path": logical,
                "source_path": str(bundle.source_paths[logical]),
                "size_bytes": len(content),
            }
            for logical, content in sorted(bundle.files.items())
        ],
    }


def bundle_main() -> int:
    """Entry point for ``nhr9300-bundle digest|inspect``."""
    parser = argparse.ArgumentParser(description="Validate and inspect a workflow bundle")
    parser.add_argument("action", choices=("digest", "inspect"))
    parser.add_argument("profile", type=Path)
    parser.add_argument("--hardware", action="store_true")
    args = parser.parse_args()
    bundle = WorkflowBundle.load(args.profile, hardware=args.hardware)
    if args.action == "digest":
        print(bundle.digest)
    else:
        print(json.dumps(_bundle_description(bundle), indent=2))
    return 0
