from __future__ import annotations

import json

import pytest

from nhr9300 import NHR9300, StaticInterlockProvider
from nhr9300.backends.simulator import SimulatedBackend
from nhr9300.capability_manifest import (
    CapabilityManifest,
    capture_capability_manifest,
)
from nhr9300.errors import NHRValidationError
from nhr9300.service import InstrumentManager
from nhr9300.types import SafetyLimits


def _instrument(backend: SimulatedBackend) -> NHR9300:
    return NHR9300(
        backend.instrument_id,
        backend,
        interlocks=[StaticInterlockProvider(safe=False)],
    )


def test_capture_and_load_reviewed_333a_330v_envelope(tmp_path) -> None:
    instrument = _instrument(SimulatedBackend("sim-cap", initial_voltage_v=300.0))
    with instrument:
        manifest = capture_capability_manifest(
            instrument,
            instrument_id="sim-cap",
            resource_name="sim-cap",
            expected_serial_number="SIM-9300",
            max_current_a=333.0,
            max_voltage_v=330.0,
            max_power_w=100_000.0,
        )

    path = tmp_path / "capabilities.json"
    manifest.write(path)
    loaded = CapabilityManifest.load(path)

    assert loaded.identity.serial_number == "SIM-9300"
    assert loaded.effective_capabilities.charge_current_max == 333.0
    assert loaded.effective_capabilities.discharge_current_max == 333.0
    assert loaded.effective_capabilities.voltage_max == 330.0
    assert loaded.effective_capabilities.charge_power_max == 100_000.0


def test_capture_rejects_unavailable_or_oversized_capabilities() -> None:
    backend = SimulatedBackend("sim-cap")
    backend.remote = False
    instrument = _instrument(backend)
    with instrument:
        with pytest.raises(NHRValidationError, match="not remotely available"):
            capture_capability_manifest(
                instrument,
                instrument_id="sim-cap",
                resource_name="sim-cap",
                expected_serial_number="SIM-9300",
                max_current_a=333.0,
                max_voltage_v=330.0,
                max_power_w=100_000.0,
            )

    backend.remote = True
    with instrument:
        with pytest.raises(NHRValidationError, match="exceeds the observed"):
            capture_capability_manifest(
                instrument,
                instrument_id="sim-cap",
                resource_name="sim-cap",
                expected_serial_number="SIM-9300",
                max_current_a=334.0,
                max_voltage_v=330.0,
                max_power_w=100_000.0,
            )


def test_manifest_drives_limit_validation_without_capability_reread(tmp_path) -> None:
    source = _instrument(SimulatedBackend("sim-cap", initial_voltage_v=300.0))
    with source:
        manifest = capture_capability_manifest(
            source,
            instrument_id="sim-cap",
            resource_name="sim-cap",
            expected_serial_number="SIM-9300",
            max_current_a=333.0,
            max_voltage_v=330.0,
            max_power_w=100_000.0,
        )

    class NoCapabilityReadBackend(SimulatedBackend):
        def read_capabilities(self):  # type: ignore[no-untyped-def]
            raise AssertionError("capabilities must come from the manifest")

    backend = NoCapabilityReadBackend("sim-cap", initial_voltage_v=300.0)
    instrument = NHR9300(
        "sim-cap",
        backend,
        interlocks=[StaticInterlockProvider(safe=False)],
        capability_manifest=manifest,
    )
    with instrument:
        instrument.configure_safety_limits(
            SafetyLimits(
                charge_current=333.0,
                charge_voltage_max=330.0,
                charge_power=100_000.0,
                discharge_current=333.0,
                discharge_voltage_min=1.0,
                discharge_power=100_000.0,
                approved=True,
                profile_name="validated 333 A / 330 V envelope",
            )
        )
        assert instrument.read_capabilities() == manifest.effective_capabilities


def test_manifest_loader_rejects_identity_placeholder(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_at_utc": "2026-09-17T00:00:00+00:00",
                "instrument_id": "nhr",
                "resource_name": "DC PM 1",
                "identity": {
                    "logical_name": "DC PM 1",
                    "serial_number": "None",
                    "part_number": "None",
                },
                "observed_capabilities": {
                    "voltage_min": 0,
                    "voltage_max": 600,
                    "charge_current_max": 333,
                    "discharge_current_max": 333,
                    "charge_power_max": 100000,
                    "discharge_power_max": 100000,
                },
                "effective_capabilities": {
                    "voltage_min": 0,
                    "voltage_max": 330,
                    "charge_current_max": 333,
                    "discharge_current_max": 333,
                    "charge_power_max": 100000,
                    "discharge_power_max": 100000,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(NHRValidationError, match="serial_number is unavailable"):
        CapabilityManifest.load(path)


def test_service_loads_relative_ivi_capability_manifest(tmp_path) -> None:
    source = _instrument(SimulatedBackend("DC PM 1", initial_voltage_v=300.0))
    with source:
        manifest = capture_capability_manifest(
            source,
            instrument_id="nhr-79503",
            resource_name="DC PM 1",
            expected_serial_number="SIM-9300",
            max_current_a=333.0,
            max_voltage_v=330.0,
            max_power_w=100_000.0,
        )
    manifest_path = tmp_path / "capabilities" / "nhr-79503.json"
    manifest.write(manifest_path)
    config_path = tmp_path / "service.json"
    config_path.write_text("{}", encoding="utf-8")

    manager = InstrumentManager(
        {
            "output_dir": str(tmp_path / "runs"),
            "instruments": [
                {
                    "id": "nhr-79503",
                    "backend": "ivi",
                    "logical_name": "DC PM 1",
                    "capability_manifest": "capabilities/nhr-79503.json",
                }
            ],
            "workflow_registry": [],
        },
        config_path=config_path,
    )

    loaded = manager.instruments["nhr-79503"].instrument._capability_manifest
    assert loaded is not None
    assert loaded.effective_capabilities.voltage_max == 330.0
