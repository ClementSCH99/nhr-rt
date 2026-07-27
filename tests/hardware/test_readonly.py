from __future__ import annotations

import os

import pytest

from nhr9300 import NHR9300, StaticInterlockProvider
from nhr9300.backends.ivi import IVIBackend


@pytest.mark.hardware_readonly
def test_readonly_identity_status_and_measurement() -> None:
    resource = os.environ.get("NHR9300_RESOURCE")
    if not resource:
        pytest.skip("Set NHR9300_RESOURCE to a configured logical name")
    instrument = NHR9300(
        "hardware",
        IVIBackend("hardware", resource),
        interlocks=[StaticInterlockProvider(safe=False)],
    )
    with instrument:
        assert instrument.read_identity().serial_number
        assert instrument.read_status().connected
        instrument.read_measurement()
