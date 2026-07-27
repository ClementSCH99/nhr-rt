from __future__ import annotations

import json
import os

import pytest

from nhr9300 import NHR9300, StaticInterlockProvider
from nhr9300.acquisition import AcquisitionCollector
from nhr9300.backends.ivi import IVIBackend
from nhr9300.routines import RoutineRunner, routine_from_mapping
from nhr9300.types import RoutineState


@pytest.mark.hardware_energizing
def test_supervised_constant_current_hold(request: pytest.FixtureRequest, tmp_path) -> None:
    """Energizing wrapper with three gates: CLI flag, profile and exact ACK."""
    if os.environ.get("NHR9300_ENERGIZING_ACK") != "SUPERVISED_BENCH_READY":
        pytest.skip("Set NHR9300_ENERGIZING_ACK=SUPERVISED_BENCH_READY")
    resource = os.environ.get("NHR9300_RESOURCE")
    if not resource:
        pytest.skip("Set NHR9300_RESOURCE to a configured logical name")
    profile_path = request.config.getoption("--bench-profile")
    definition = json.loads(profile_path.read_text(encoding="utf-8"))
    routine = routine_from_mapping(definition)
    instrument = NHR9300(
        "hardware",
        IVIBackend("hardware", resource),
        interlocks=[StaticInterlockProvider(safe=True, name="operator_supervised")],
    )
    collector = AcquisitionCollector(
        instrument, rate_hz=5, csv_path=tmp_path / "hardware-cc-hold.csv"
    )
    with instrument:
        result = RoutineRunner(instrument, collector).run(routine)
        status = instrument.read_status()
    assert result.state == RoutineState.PASSED
    assert status.state.name == "STANDBY"
    assert status.enabled is False
