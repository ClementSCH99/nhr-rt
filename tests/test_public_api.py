from __future__ import annotations

from nhr9300 import (
    ExternalSnapshotPublisher,
    ExternalSnapshotReceipt,
    InterlockSnapshot,
    NHRServiceClient,
    RuntimeEventEnvelope,
    RuntimeSnapshot,
    ServiceConfiguration,
    WorkflowBundle,
    WorkflowConfiguration,
    load_cc_profile,
    load_workflow_profile,
    validate_cc_profile,
    validate_workflow_profile,
)
from nhr9300.cc_profiles import (
    load_cc_profile as direct_load_cc_profile,
    validate_cc_profile as direct_validate_cc_profile,
)
from nhr9300.client import (
    ExternalSnapshotPublisher as DirectExternalSnapshotPublisher,
    ExternalSnapshotReceipt as DirectExternalSnapshotReceipt,
    InterlockSnapshot as DirectInterlockSnapshot,
    NHRServiceClient as DirectClient,
    RuntimeEventEnvelope as DirectRuntimeEventEnvelope,
    RuntimeSnapshot as DirectRuntimeSnapshot,
    ServiceConfiguration as DirectServiceConfiguration,
)
from nhr9300.profiles import (
    WorkflowConfiguration as DirectWorkflowConfiguration,
    load_workflow_profile as direct_load_workflow_profile,
    validate_workflow_profile as direct_validate_workflow_profile,
)
from nhr9300.workflow_registry import WorkflowBundle as DirectWorkflowBundle


def test_service_client_is_part_of_public_api() -> None:
    assert NHRServiceClient is DirectClient
    assert ExternalSnapshotPublisher is DirectExternalSnapshotPublisher
    assert ServiceConfiguration is DirectServiceConfiguration
    assert ExternalSnapshotReceipt is DirectExternalSnapshotReceipt
    assert InterlockSnapshot is DirectInterlockSnapshot
    assert RuntimeSnapshot is DirectRuntimeSnapshot
    assert RuntimeEventEnvelope is DirectRuntimeEventEnvelope


def test_cc_profile_contract_is_part_of_public_api() -> None:
    assert load_cc_profile is direct_load_cc_profile
    assert validate_cc_profile is direct_validate_cc_profile


def test_workflow_contract_is_part_of_public_api() -> None:
    assert WorkflowConfiguration is DirectWorkflowConfiguration
    assert load_workflow_profile is direct_load_workflow_profile
    assert validate_workflow_profile is direct_validate_workflow_profile
    assert WorkflowBundle is DirectWorkflowBundle
