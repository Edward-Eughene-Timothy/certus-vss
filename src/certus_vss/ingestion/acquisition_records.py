
"""Validation and record construction for broad CARLA acquisition."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .acquisition_catalog import (
    Availability,
    DataType,
    Provenance,
    get_acquisition_signal,
)


@dataclass(frozen=True)
class AcquisitionRecord:
    timestamp_utc_s: float
    signal: str
    value: Any
    unit: str
    source: str
    provenance: Provenance
    availability: Availability


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _validate_value(value: Any, data_type: DataType) -> None:
    if data_type == DataType.FLOAT:
        if not _is_finite_number(value):
            raise ValueError("Expected a finite numeric value")

    elif data_type == DataType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("Expected an integer value")

    elif data_type == DataType.BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError("Expected a boolean value")

    elif data_type == DataType.STRING:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Expected a non-empty string")

    elif data_type == DataType.VECTOR3:
        if not isinstance(value, (tuple, list)) or len(value) != 3:
            raise ValueError("Expected a three-component vector")
        if not all(_is_finite_number(component) for component in value):
            raise ValueError("Vector components must be finite numbers")

    elif data_type == DataType.OBJECT:
        if not isinstance(value, (dict, list)):
            raise ValueError("Expected an object or list")

    elif data_type == DataType.BINARY_REFERENCE:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Expected a non-empty sensor data reference")


def make_acquisition_record(
    *,
    timestamp_utc_s: float,
    signal: str,
    value: Any,
    unit: str,
    source: str,
    provenance: Provenance,
    availability: Availability,
) -> AcquisitionRecord:
    """Validate and construct one acquisition observation.

    Timestamps are UTC epoch seconds, not CARLA simulation elapsed time.
    Simulation timestamps should be recorded separately.
    """
    if not _is_finite_number(timestamp_utc_s) or timestamp_utc_s <= 0:
        raise ValueError("timestamp_utc_s must be a positive finite number")

    if not isinstance(signal, str) or not signal.strip():
        raise ValueError("signal identifier is required")

    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")

    if not isinstance(provenance, Provenance):
        raise ValueError("provenance must be a Provenance value")

    if not isinstance(availability, Availability):
        raise ValueError("availability must be an Availability value")

    catalog_entry = get_acquisition_signal(signal)
    if catalog_entry is None:
        raise ValueError(f"Unknown acquisition signal: {signal}")

    if unit != catalog_entry.unit:
        raise ValueError(
            f"Unit mismatch for {signal}: expected {catalog_entry.unit}, got {unit}"
        )

    if availability == Availability.UNAVAILABLE:
        if value is not None:
            raise ValueError("Unavailable measurements must have value=None")
        if provenance != Provenance.NOT_PROVIDED:
            raise ValueError("Unavailable measurements must use NOT_PROVIDED provenance")

    else:
        if value is None:
            raise ValueError("Available measurements must contain a value")
        if provenance == Provenance.NOT_PROVIDED:
            raise ValueError("Available measurements cannot use NOT_PROVIDED provenance")
        _validate_value(value, catalog_entry.data_type)

    if (
        catalog_entry.availability == Availability.UNAVAILABLE
        and availability != Availability.UNAVAILABLE
    ):
        raise ValueError(f"{signal} is unavailable in the configured catalog")

    return AcquisitionRecord(
        timestamp_utc_s=float(timestamp_utc_s),
        signal=signal,
        value=value,
        unit=unit,
        source=source,
        provenance=provenance,
        availability=availability,
    )
