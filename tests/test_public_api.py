from __future__ import annotations

from nhr9300 import (
    NHRServiceClient,
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
from nhr9300.client import NHRServiceClient as DirectClient
from nhr9300.profiles import (
    WorkflowConfiguration as DirectWorkflowConfiguration,
    load_workflow_profile as direct_load_workflow_profile,
    validate_workflow_profile as direct_validate_workflow_profile,
)
from nhr9300.workflow_registry import WorkflowBundle as DirectWorkflowBundle


def test_service_client_is_part_of_public_api() -> None:
    assert NHRServiceClient is DirectClient


def test_cc_profile_contract_is_part_of_public_api() -> None:
    assert load_cc_profile is direct_load_cc_profile
    assert validate_cc_profile is direct_validate_cc_profile


def test_workflow_contract_is_part_of_public_api() -> None:
    assert WorkflowConfiguration is DirectWorkflowConfiguration
    assert load_workflow_profile is direct_load_workflow_profile
    assert validate_workflow_profile is direct_validate_workflow_profile
    assert WorkflowBundle is DirectWorkflowBundle
