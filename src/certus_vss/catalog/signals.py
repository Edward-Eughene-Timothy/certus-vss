"""
signals.py: Public catalog interface for canonical VSS signal paths and units.
Single source of truth across ingestion, storage, analytics, and presentation.
"""
from __future__ import annotations

from typing import Optional

CANONICAL_VSS_SIGNALS: tuple[str, ...] = (
    "Vehicle.Speed",
    "Vehicle.Powertrain.TractionBattery.Temperature",
    "Vehicle.Powertrain.TractionBattery.Current",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge",
    "Vehicle.Cabin.HVAC.PowerDraw",
    "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
)

CANONICAL_VSS_UNITS: dict[str, str] = {
    "Vehicle.Speed": "km/h",
    "Vehicle.Powertrain.TractionBattery.Temperature": "celsius",
    "Vehicle.Powertrain.TractionBattery.Current": "amperes",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge": "percent",
    "Vehicle.Cabin.HVAC.PowerDraw": "watts",
    "Vehicle.Powertrain.ElectricMotor.CoolantTemperature": "celsius",
}


def get_signal_unit(signal_path: str) -> Optional[str]:
    """Return canonical unit for a VSS signal path, or None if unrecognized."""
    if not isinstance(signal_path, str):
        return None
    return CANONICAL_VSS_UNITS.get(signal_path.strip())
