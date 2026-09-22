"""One-time, identity-bound snapshots of physical NHR capabilities."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .errors import NHRValidationError
from .evidence import atomic_write_json
from .types import Capabilities, Identity, OperatingState


CAPABILITY_MANIFEST_SCHEMA_VERSION = 1


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NHRValidationError(f"{name} must be an object")
    return value


def _valid_identity_value(value: str) -> bool:
    return bool(value.strip()) and value.strip().lower() not in {"none", "null"}


def _positive_finite(value: float, name: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise NHRValidationError(f"{name} must be finite and greater than zero")
    return parsed


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """Observed hardware capabilities plus a reviewed operating envelope."""

    instrument_id: str
    resource_name: str
    identity: Identity
    observed_capabilities: Capabilities
    effective_capabilities: Capabilities
    captured_at_utc: str
    schema_version: int = CAPABILITY_MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "captured_at_utc": self.captured_at_utc,
            "instrument_id": self.instrument_id,
            "resource_name": self.resource_name,
            "identity": asdict(self.identity),
            "observed_capabilities": asdict(self.observed_capabilities),
            "effective_capabilities": asdict(self.effective_capabilities),
        }

    def validate(self) -> None:
        if self.schema_version != CAPABILITY_MANIFEST_SCHEMA_VERSION:
            raise NHRValidationError(
                f"Unsupported capability manifest schema: {self.schema_version}"
            )
        if not self.instrument_id.strip() or not self.resource_name.strip():
            raise NHRValidationError(
                "Capability manifest requires instrument_id and resource_name"
            )
        for field_name in ("logical_name", "serial_number", "part_number"):
            if not _valid_identity_value(str(getattr(self.identity, field_name))):
                raise NHRValidationError(
                    f"Capability manifest identity.{field_name} is unavailable"
                )
        observed = self.observed_capabilities
        effective = self.effective_capabilities
        for name in (
            "voltage_max",
            "charge_current_max",
            "discharge_current_max",
            "charge_power_max",
            "discharge_power_max",
        ):
            observed_value = _positive_finite(getattr(observed, name), f"observed {name}")
            effective_value = _positive_finite(
                getattr(effective, name), f"effective {name}"
            )
            if effective_value > observed_value:
                raise NHRValidationError(
                    f"Effective {name} exceeds the observed hardware capability"
                )
        if effective.voltage_min < observed.voltage_min:
            raise NHRValidationError(
                "Effective voltage_min is below the observed hardware capability"
            )

    def verify_binding(
        self,
        *,
        instrument_id: str,
        resource_name: str,
        identity: Identity | None = None,
    ) -> None:
        self.validate()
        if self.instrument_id != instrument_id:
            raise NHRValidationError(
                "Capability manifest instrument_id does not match configuration"
            )
        if self.resource_name != resource_name:
            raise NHRValidationError(
                "Capability manifest resource_name does not match configuration"
            )
        if identity is None:
            return
        for field_name in ("logical_name", "serial_number", "part_number"):
            if str(getattr(identity, field_name)) != str(
                getattr(self.identity, field_name)
            ):
                raise NHRValidationError(
                    f"Capability manifest identity mismatch: {field_name}"
                )

    def write(self, path: Path) -> None:
        self.validate()
        atomic_write_json(path, self.to_dict())

    @classmethod
    def load(cls, path: Path) -> "CapabilityManifest":
        try:
            root = json.loads(path.read_text(encoding="utf-8"))
            mapping = _mapping(root, "capability manifest")
            manifest = cls(
                schema_version=int(mapping.get("schema_version", 0)),
                captured_at_utc=str(mapping["captured_at_utc"]),
                instrument_id=str(mapping["instrument_id"]),
                resource_name=str(mapping["resource_name"]),
                identity=Identity(**_mapping(mapping["identity"], "identity")),
                observed_capabilities=Capabilities(
                    **_mapping(
                        mapping["observed_capabilities"], "observed_capabilities"
                    )
                ),
                effective_capabilities=Capabilities(
                    **_mapping(
                        mapping["effective_capabilities"], "effective_capabilities"
                    )
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise NHRValidationError(f"Invalid capability manifest {path}: {exc}") from exc
        manifest.validate()
        return manifest


def capture_capability_manifest(
    instrument: Any,
    *,
    instrument_id: str,
    resource_name: str,
    expected_serial_number: str,
    max_current_a: float,
    max_voltage_v: float,
    max_power_w: float,
) -> CapabilityManifest:
    """Capture a disabled, remote instrument and constrain its usable envelope."""
    status = instrument.read_status()
    if not status.remote:
        raise NHRValidationError(
            "The NHR is not remotely available; capability capture is invalid"
        )
    if status.enabled or status.state not in (OperatingState.OFF, OperatingState.STANDBY):
        raise NHRValidationError(
            "Capability capture requires disabled output in OFF or STANDBY"
        )
    identity = instrument.read_identity()
    if identity.serial_number != expected_serial_number:
        raise NHRValidationError(
            "Connected NHR serial number does not match --expected-serial-number"
        )
    observed = instrument.read_capabilities()
    effective = replace(
        observed,
        voltage_max=_positive_finite(max_voltage_v, "max_voltage_v"),
        charge_current_max=_positive_finite(max_current_a, "max_current_a"),
        discharge_current_max=_positive_finite(max_current_a, "max_current_a"),
        charge_power_max=_positive_finite(max_power_w, "max_power_w"),
        discharge_power_max=_positive_finite(max_power_w, "max_power_w"),
    )
    manifest = CapabilityManifest(
        instrument_id=instrument_id,
        resource_name=resource_name,
        identity=identity,
        observed_capabilities=observed,
        effective_capabilities=effective,
        captured_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    manifest.validate()
    return manifest
