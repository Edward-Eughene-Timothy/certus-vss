from certus_vss.ingestion.acquisition_catalog import (
    ACQUISITION_CATALOG,
    AcquisitionCategory,
    Availability,
    Provenance,
    get_acquisition_signal,
    get_signals_by_category,
    validate_catalog,
)
from certus_vss.ingestion.ring_buffer import SIGNAL_PATHS


def test_catalog_has_unique_identifiers_and_valid_metadata():
    validate_catalog()
    identifiers = [item.identifier for item in ACQUISITION_CATALOG]
    assert len(identifiers) == len(set(identifiers))
    assert all(item.unit and item.description for item in ACQUISITION_CATALOG)


def test_all_six_acquisition_categories_are_present():
    categories = {item.category for item in ACQUISITION_CATALOG}
    assert categories == set(AcquisitionCategory)


def test_five_ev_signals_are_explicitly_unavailable():
    ev_paths = {
        "Vehicle.Powertrain.TractionBattery.Temperature",
        "Vehicle.Powertrain.TractionBattery.Current",
        "Vehicle.Powertrain.TractionBattery.StateOfCharge",
        "Vehicle.Cabin.HVAC.PowerDraw",
        "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
    }

    for path in ev_paths:
        item = get_acquisition_signal(path)
        assert item is not None
        assert item.availability == Availability.UNAVAILABLE
        assert item.provenance == Provenance.NOT_PROVIDED


def test_broad_acquisition_does_not_expand_diagnostic_ring_buffer():
    assert len(SIGNAL_PATHS) == 6
    assert get_acquisition_signal("vehicle.position") is not None
    assert "vehicle.position" not in SIGNAL_PATHS


def test_category_filter_returns_only_matching_entries():
    sensors = get_signals_by_category(AcquisitionCategory.SENSOR)
    assert len(sensors) == 5
    assert all(item.category == AcquisitionCategory.SENSOR for item in sensors)


def test_unknown_identifier_returns_none():
    assert get_acquisition_signal("vehicle.unknown") is None
