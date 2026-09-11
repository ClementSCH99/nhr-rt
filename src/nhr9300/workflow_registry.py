"""Immutable startup registry for locally approved workflow bundles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from .errors import NHRPolicyError, NHRValidationError
from .profiles import WorkflowConfiguration, load_workflow_profile, validate_workflow_profile


BUNDLE_SCHEMA_VERSION = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _logical_csv_paths(profile_bytes: bytes) -> tuple[str, ...]:
    root = json.loads(profile_bytes.decode("utf-8"))
    paths: list[str] = []
    for stage in root.get("stages", []):
        raw = stage.get("csv_path")
        if raw is None:
            continue
        text = str(raw).replace("\\", "/")
        logical = PurePosixPath(text)
        if logical.is_absolute() or ".." in logical.parts or not logical.parts:
            raise NHRValidationError(
                f"Dynamic profile path must remain inside the bundle: {raw}"
            )
        normalized = logical.as_posix()
        if normalized in paths:
            raise NHRValidationError(f"Duplicate dynamic profile path: {normalized}")
        paths.append(normalized)
    return tuple(paths)


def _bundle_digest(files: Mapping[str, bytes]) -> str:
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "files": [
            {
                "path": path,
                "size_bytes": len(files[path]),
                "sha256": _sha256(files[path]),
            }
            for path in sorted(files)
        ],
    }
    canonical = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{_sha256(canonical)}"


@dataclass(frozen=True, slots=True)
class WorkflowBundle:
    """Immutable snapshot of a workflow JSON file and referenced CSV files.

    Use :meth:`load`; do not construct this dataclass manually. ``files`` uses
    canonical bundle-relative names while ``source_paths`` retains the original
    local locations. ``digest`` covers the bytes of every member.
    """
    profile_path: Path
    configuration: WorkflowConfiguration
    files: Mapping[str, bytes]
    source_paths: Mapping[str, Path]
    digest: str

    @classmethod
    def load(cls, profile_path: Path, *, hardware: bool) -> WorkflowBundle:
        """Validate and snapshot a profile plus every referenced dynamic CSV."""
        source = profile_path.resolve()
        profile_bytes = source.read_bytes()
        logical_csv = _logical_csv_paths(profile_bytes)
        files: dict[str, bytes] = {"workflow.json": profile_bytes}
        sources: dict[str, Path] = {"workflow.json": source}
        for logical in logical_csv:
            target = (source.parent / Path(logical)).resolve()
            try:
                target.relative_to(source.parent.resolve())
            except ValueError as exc:
                raise NHRValidationError(
                    f"Dynamic profile escapes the bundle: {logical}"
                ) from exc
            files[logical] = target.read_bytes()
            sources[logical] = target
        configuration = load_workflow_profile(source)
        validate_workflow_profile(configuration, hardware=hardware)
        return cls(
            source,
            configuration,
            MappingProxyType(files),
            MappingProxyType(sources),
            _bundle_digest(files),
        )

    def verify_unchanged(self) -> None:
        """Reject source-file drift relative to the startup snapshot."""
        try:
            observed = {
                logical: source.read_bytes()
                for logical, source in self.source_paths.items()
            }
        except OSError as exc:
            raise NHRPolicyError(
                "Workflow bundle is unavailable or changed after service startup"
            ) from exc
        if _bundle_digest(observed) != self.digest:
            raise NHRPolicyError(
                "Workflow bundle changed after service startup; restart required"
            )

    def materialize(self, target: Path) -> Path:
        """Write the frozen bundle to a new directory and return workflow.json."""
        target.mkdir(parents=True, exist_ok=False)
        for logical, content in self.files.items():
            path = target / Path(logical)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return target / "workflow.json"


@dataclass(frozen=True, slots=True)
class WorkflowRegistryEntry:
    workflow_id: str
    instrument_id: str
    expected_resource: str
    expected_serial_number: str
    approved: bool
    bundle: WorkflowBundle | None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.approved and self.bundle is not None and self.error is None

    def public(self) -> dict[str, Any]:
        configuration = self.bundle.configuration if self.bundle else None
        return {
            "workflow_id": self.workflow_id,
            "instrument_id": self.instrument_id,
            "bundle_digest": self.bundle.digest if self.bundle else None,
            "available": self.available,
            "error": self.error,
            "profile_name": (
                configuration.workflow_limits.profile_name if configuration else None
            ),
            "test_description": (
                configuration.test_description if configuration else None
            ),
            "stage_count": len(configuration.stages) if configuration else 0,
            "external_interlock_count": (
                len(configuration.external_interlocks) if configuration else 0
            ),
            "arm_lease_renewal_enabled": (
                configuration.workflow_limits.arm_lease_renewal_enabled
                if configuration
                else False
            ),
        }


class WorkflowRegistry:
    """Frozen collection of workflow bundles loaded from local configuration."""

    def __init__(self, entries: Mapping[str, WorkflowRegistryEntry]) -> None:
        self._entries = dict(entries)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        base_dir: Path,
        instrument_backends: Mapping[str, str],
    ) -> WorkflowRegistry:
        entries: dict[str, WorkflowRegistryEntry] = {}
        raw_registry = config.get("workflow_registry", [])
        if not isinstance(raw_registry, list):
            raise NHRValidationError("workflow_registry must be an array")
        for raw in raw_registry:
            if not isinstance(raw, Mapping):
                raise NHRValidationError("workflow registry entries must be objects")
            item = dict(raw)
            workflow_id = str(item.get("workflow_id", "")).strip()
            instrument_id = str(item.get("instrument_id", "")).strip()
            expected_resource = str(item.get("expected_resource", "")).strip()
            expected_serial = str(item.get("expected_serial_number", "")).strip()
            approved = item.get("approved", False)
            if not workflow_id or workflow_id in entries:
                raise NHRValidationError(
                    "workflow_id values must be non-empty and unique"
                )
            if instrument_id not in instrument_backends:
                raise NHRValidationError(
                    f"Unknown registry instrument: {instrument_id}"
                )
            if not isinstance(approved, bool):
                raise NHRValidationError("workflow registry approved must be boolean")
            bundle: WorkflowBundle | None = None
            error: str | None = None
            try:
                raw_path = Path(str(item["profile_path"]))
                profile_path = raw_path if raw_path.is_absolute() else base_dir / raw_path
                bundle = WorkflowBundle.load(
                    profile_path,
                    hardware=instrument_backends[instrument_id] == "ivi",
                )
                if bundle.configuration.expected_resource != expected_resource:
                    raise NHRValidationError(
                        "Registry expected_resource does not match workflow profile"
                    )
                if bundle.configuration.expected_serial_number != expected_serial:
                    raise NHRValidationError(
                        "Registry expected_serial_number does not match workflow profile"
                    )
                expected_digest = str(item.get("expected_bundle_digest", ""))
                if expected_digest != bundle.digest:
                    raise NHRValidationError(
                        "Configured bundle digest does not match local workflow bytes"
                    )
                if not approved:
                    raise NHRPolicyError("Workflow registry entry is not approved")
            except (KeyError, OSError, ValueError, NHRPolicyError) as exc:
                error = f"{type(exc).__name__}: {exc}"
                bundle = None
            entries[workflow_id] = WorkflowRegistryEntry(
                workflow_id,
                instrument_id,
                expected_resource,
                expected_serial,
                approved,
                bundle,
                error,
            )
        return cls(entries)

    def list_for(self, instrument_id: str) -> list[dict[str, Any]]:
        return [
            entry.public()
            for entry in self._entries.values()
            if entry.instrument_id == instrument_id
        ]

    def require(
        self,
        instrument_id: str,
        workflow_id: str,
        expected_digest: str,
    ) -> WorkflowRegistryEntry:
        entry = self._entries.get(workflow_id)
        if entry is None or entry.instrument_id != instrument_id:
            raise NHRValidationError(f"Unknown workflow_id: {workflow_id}")
        if not entry.available or entry.bundle is None:
            raise NHRPolicyError(entry.error or "Workflow is not approved")
        if expected_digest != entry.bundle.digest:
            raise NHRPolicyError("Client bundle digest does not match the registry")
        entry.bundle.verify_unchanged()
        return entry


__all__ = [
    "WorkflowBundle",
    "WorkflowRegistry",
    "WorkflowRegistryEntry",
]
