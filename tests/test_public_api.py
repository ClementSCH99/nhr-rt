from __future__ import annotations

from nhr9300 import NHRServiceClient, load_cc_profile, validate_cc_profile
from nhr9300.cc_profiles import (
    load_cc_profile as direct_load_cc_profile,
    validate_cc_profile as direct_validate_cc_profile,
)
from nhr9300.client import NHRServiceClient as DirectClient


def test_service_client_is_part_of_public_api() -> None:
    assert NHRServiceClient is DirectClient


def test_cc_profile_contract_is_part_of_public_api() -> None:
    assert load_cc_profile is direct_load_cc_profile
    assert validate_cc_profile is direct_validate_cc_profile
