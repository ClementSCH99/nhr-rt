from __future__ import annotations

from nhr9300 import NHRServiceClient
from nhr9300.client import NHRServiceClient as DirectClient


def test_service_client_is_part_of_public_api() -> None:
    assert NHRServiceClient is DirectClient
