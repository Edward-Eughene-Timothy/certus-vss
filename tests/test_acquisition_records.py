import pytest

from certus_vss.ingestion.acquisition_catalog import (
    Availability,
    Provenance,
)
from certus_vss.ingestion.acquisition_records import (
    make_acquisition_record,
)


def make_record(**overrides):
    values = {
        "timestamp_utc_s": 1_700_000_000.0,
        "signal": "vehicle.speed",
        "value": 36.0,
        "unit": "km/h",
        "source": "CARLA",
        "provenance": Provenance.CARLA_CALCULATED,
        "availability": Availability.AVAILABLE,
    }
    values.update(overrides)
    return make_acquisition_record(**values)


def test_valid_record_is_created():
    record = make_record()
    assert record.value == 36.0
    assert record.unit == "km/h"
    assert record.source == "CARLA"


@pytest.mark.parametrize("timestamp", [0, -1, float("nan"), float("inf")])
def test_invalid_timestamps_are_rejected(timestamp):
    with pytest.raises(ValueError):
        make_record(timestamp_utc_s=timestamp)


def test_unknown_signal_is_rejected():
    with pytest.raises(ValueError):
        make_record(signal="vehicle.unknown")


def test_unit_mismatch_is_rejected():
    with pytest.raises(ValueError):
        make_record(unit="mph")


def test_non_finite_measurement_is_rejected():
    with pytest.raises(ValueError):
        make_record(value=float("nan"))


def test_unavailable_measurement_must_remain_null():
    with pytest.raises(ValueError):
        make_record(
            signal="Vehicle.Powertrain.TractionBattery.Temperature",
            value=25.0,
            unit="celsius",
            provenance=Provenance.CARLA_MEASURED,
            availability=Availability.UNAVAILABLE,
        )


def test_unavailable_measurement_can_be_recorded_as_null():
    record = make_record(
        signal="Vehicle.Powertrain.TractionBattery.Temperature",
        value=None,
        unit="celsius",
        provenance=Provenance.NOT_PROVIDED,
        availability=Availability.UNAVAILABLE,
    )
    assert record.value is None
    assert record.availability == Availability.UNAVAILABLE


def test_boolean_is_not_accepted_as_numeric_measurement():
    with pytest.raises(ValueError):
        make_record(value=True)
