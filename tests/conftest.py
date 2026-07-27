from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-hardware-readonly",
        action="store_true",
        help="run read-only tests against a configured NHR9300",
    )
    parser.addoption(
        "--run-hardware-energizing",
        action="store_true",
        help="allow tests that may energize a physical NHR9300",
    )
    parser.addoption(
        "--bench-profile",
        type=Path,
        help="approved local safety profile required by energizing tests",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    readonly = config.getoption("--run-hardware-readonly")
    energizing = config.getoption("--run-hardware-energizing")
    profile = config.getoption("--bench-profile")
    for item in items:
        if "hardware_readonly" in item.keywords and not readonly:
            item.add_marker(pytest.mark.skip(reason="requires --run-hardware-readonly"))
        if "hardware_energizing" in item.keywords:
            if not energizing or profile is None:
                item.add_marker(
                    pytest.mark.skip(
                        reason="requires --run-hardware-energizing and --bench-profile"
                    )
                )
