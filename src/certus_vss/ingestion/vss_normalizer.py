"""Normalize incoming signal names and values to the repository VSS catalog."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .ring_buffer import SIGNAL_PATHS

# Deliberately small initial alias map. Extend alongside DBC/CARLA adapters.
_ALIASES = {
    "speed": "Vehicle.Speed",
    "vehicle_speed": "Vehicle.Speed",
    "Vehicle.Speed": "Vehicle.Speed",
    "battery_temperature": "Vehicle.Powertrain.TractionBattery.Temperature",
    "battery_temp_c": "Vehicle.Powertrain.TractionBattery.Temperature",
    "Vehicle.Powertrain.TractionBattery.Temperature": "Vehicle.Powertrain.TractionBattery.Temperature",
    "battery_current": "Vehicle.Powertrain.TractionBattery.Current",
    "Vehicle.Powertrain.TractionBattery.Current": "Vehicle.Powertrain.TractionBattery.Current",
    "battery_soc": "Vehicle.Powertrain.TractionBattery.StateOfCharge",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge": "Vehicle.Powertrain.TractionBattery.StateOfCharge",
    "hvac_power": "Vehicle.Cabin.HVAC.PowerDraw",
    "Vehicle.Cabin.HVAC.PowerDraw": "Vehicle.Cabin.HVAC.PowerDraw",
    "motor_coolant_temperature": "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
    "Vehicle.Powertrain.ElectricMotor.CoolantTemperature": "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
}
_UNITS = {
    "Vehicle.Speed": "km/h",
    "Vehicle.Powertrain.TractionBattery.Temperature": "celsius",
    "Vehicle.Powertrain.TractionBattery.Current": "amperes",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge": "percent",
    "Vehicle.Cabin.HVAC.PowerDraw": "watts",
    "Vehicle.Powertrain.ElectricMotor.CoolantTemperature": "celsius",
}


def normalize_vss(signal_key: str, value: float | Mapping[str, Any], unit: str | None = None) -> dict[str, Any]:
    """Return a validated ``{path, value, unit}`` record for a known VSS signal.

    Speed may be supplied in km/h or m/s. Unknown aliases, units, non-numeric
    values, booleans, NaN and infinities are rejected with ``ValueError``.
    """
    if signal_key not in _ALIASES:
        raise ValueError(f"unrecognized signal key: {signal_key}")
    path = _ALIASES[signal_key]
    if isinstance(value, Mapping):
        unit = value.get("unit", unit)
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("signal value must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("signal value must be finite")
    unit_norm = unit.lower().strip() if isinstance(unit, str) else None
    if path == "Vehicle.Speed" and unit_norm in {"m/s", "mps", "meter/second", "meters/second"}:
        number *= 3.6
    elif unit_norm is not None and unit_norm not in {_UNITS[path].lower(), "km/h" if path == "Vehicle.Speed" else _UNITS[path].lower()}:
        raise ValueError(f"unsupported unit {unit!r} for {path}")
    if path not in SIGNAL_PATHS:
        raise ValueError(f"signal {path} is not in the configured ring-buffer catalog")
    return {"path": path, "value": number, "unit": _UNITS[path]}
