"""Safely validate an IVI-COM connection to the configured NHR9300 module.

This script invokes read-only driver members only after initialization. It does
not explicitly reset the instrument or set its operating state, output
enablement, or programmed setpoints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import comtypes
import comtypes.client


DRIVER_DLL = Path(
    r"C:\Program Files (x86)\IVI Foundation\IVI\Bin\NHRDCPowerModule.dll"
)
RESOURCE_NAME = "DC PM 1"

OPERATING_STATE_NAMES: dict[int, str] = {
    0: "off",
    1: "standby",
    2: "charge",
    3: "discharge",
    4: "battery_emulation",
}


def print_read_only_status(driver: Any) -> None:
    """Print a minimal status snapshot from an initialized driver session."""
    operation = driver.Input.Operation
    maintenance = driver.Maintenance
    operating_state = operation.OperatingState

    print("--- Read-only status ---")
    print(f"Remote communication : {driver.Remote.State}")
    print(f"Module enabled       : {driver.Input.Enabled}")
    print(
        "Operating state      : "
        f"{OPERATING_STATE_NAMES.get(operating_state, f'unknown ({operating_state})')}"
    )
    print(f"Voltage setpoint (V) : {operation.Voltage}")
    print(f"Current setpoint (A) : {operation.Current}")
    print(f"Power setpoint (W)   : {operation.Power}")
    print(f"Serial number        : {maintenance.SerialNumber}")
    print(f"NHR part number      : {maintenance.PartNumber}")


def main() -> None:
    """Connect, read the status, and always release the COM session."""
    if not DRIVER_DLL.is_file():
        raise FileNotFoundError(f"NHR IVI driver not found: {DRIVER_DLL}")

    comtypes.CoInitialize()
    driver: Any | None = None
    initialized = False

    try:
        module = comtypes.client.GetModule(str(DRIVER_DLL))
        driver = comtypes.client.CreateObject(
            module.NHRDCPowerModule,
            interface=module.INHRDCPowerModule,
        )
        driver.Initialize(RESOURCE_NAME, False, False, "")
        initialized = True

        print(f"Connected logical resource: {driver.LogicalName}")
        print_read_only_status(driver)
    finally:
        if initialized and driver is not None:
            driver.Close()
            print("Session closed.")
        comtypes.CoUninitialize()


if __name__ == "__main__":
    main()
