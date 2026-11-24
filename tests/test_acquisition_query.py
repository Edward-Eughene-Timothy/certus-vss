
import json
import math
import sqlite3

import pytest

from certus_vss.storage.acquisition_store import (
    initialize_acquisition_storage,
    save_acquisition_sample,
)
from certus_vss.ingestion.acquisition_records import (
    make_acquisition_record,
)
from certus_vss.ingestion.acquisition_catalog import (
    Availability,
    Provenance,
)
from certus_vss.analytics.query_engine import (
    DeterministicQueryEngine,
)


def seed_samples(db_path):
    initialize_acquisition_storage(db_path)

    records = [
        make_acquisition_record(
            timestamp_utc_s=1700000001.0,
            signal="vehicle.speed",
            value=36.0,
            unit="km/h",
            source="carla",
            provenance=Provenance.CARLA_CALCULATED,
            availability=Availability.AVAILABLE,
        ),
        make_acquisition_record(
            timestamp_utc_s=1700000002.0,
            signal="vehicle.speed",
            value=42.0,
            unit="km/h",
            source="carla",
            provenance=Provenance.CARLA_CALCULATED,
            availability=Availability.AVAILABLE,
        ),
        make_acquisition_record(
            timestamp_utc_s=1700000003.0,
            signal="vehicle.acceleration",
            value=[1.0, 2.0, 3.0],
            unit="m/s^2",
            source="carla",
            provenance=Provenance.CARLA_MEASURED,
            availability=Availability.AVAILABLE,
        ),
    ]

    for record in records:
        save_acquisition_sample(
            record,
            db_path,
            simulation_time_s=12.5,
            actor_id=42,
        )


def test_acquisition_query_filters_and_orders_samples(tmp_path):
    db_path = tmp_path / "acquisition_query.db"
    seed_samples(db_path)

    engine = DeterministicQueryEngine(db_path=db_path)

    result = engine.get_acquisition_evidence(
        actor_id=42,
        signal="vehicle.speed",
        start_epoch_s=1700000000,
        end_epoch_s=1700000002,
    )

    assert result["status"] == "SUCCESS"
    assert result["sample_count"] == 2
    assert [s["value"] for s in result["samples"]] == [36.0, 42.0]
    assert result["samples"][0]["unit"] == "km/h"
    assert result["samples"][0]["source"] == "carla"
    assert result["samples"][0]["provenance"] == "CALCULATED_FROM_CARLA"


def test_acquisition_query_returns_no_data(tmp_path):
    db_path = tmp_path / "empty_query.db"
    seed_samples(db_path)

    engine = DeterministicQueryEngine(db_path=db_path)

    result = engine.get_acquisition_evidence(
        actor_id=999,
        signal="vehicle.speed",
        start_epoch_s=1700000000,
        end_epoch_s=1700000100,
    )

    assert result["status"] == "NO_DATA"
    assert result["sample_count"] == 0
    assert result["samples"] == []


def test_acquisition_query_preserves_vector_values(tmp_path):
    db_path = tmp_path / "vector_query.db"
    seed_samples(db_path)

    engine = DeterministicQueryEngine(db_path=db_path)

    result = engine.get_acquisition_evidence(
        actor_id=42,
        signal="vehicle.acceleration",
        start_epoch_s=1700000000,
        end_epoch_s=1700000100,
    )

    assert result["status"] == "SUCCESS"
    assert result["samples"][0]["value"] == [1.0, 2.0, 3.0]


def test_acquisition_query_rejects_non_finite_numeric_values(tmp_path):
    db_path = tmp_path / "invalid_numeric.db"
    seed_samples(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE acquisition_samples
            SET value_json = 'NaN'
            WHERE signal = 'vehicle.speed'
            """
        )

    engine = DeterministicQueryEngine(db_path=db_path)

    with pytest.raises(ValueError):
        engine.get_acquisition_evidence(
            actor_id=42,
            signal="vehicle.speed",
            start_epoch_s=1700000000,
            end_epoch_s=1700000100,
        )


def test_acquisition_query_does_not_modify_database(tmp_path):
    db_path = tmp_path / "readonly_query.db"
    seed_samples(db_path)

    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM acquisition_samples"
        ).fetchone()[0]

    engine = DeterministicQueryEngine(db_path=db_path)

    engine.get_acquisition_evidence(
        actor_id=42,
        signal="vehicle.speed",
        start_epoch_s=1700000000,
        end_epoch_s=1700000100,
    )

    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM acquisition_samples"
        ).fetchone()[0]

    assert before == after == 3

def test_build_evidence_block_routes_acquisition_query(tmp_path):
    db_path = tmp_path / "acquisition_evidence_block.db"
    seed_samples(db_path)

    engine = DeterministicQueryEngine(db_path=db_path)

    evidence_json = engine.build_evidence_block(
        query_type="acquisition",
        actor_id=42,
        signal="vehicle.speed",
        start_epoch_s=1700000000,
        end_epoch_s=1700000002,
        limit=10,
    )

    result = json.loads(evidence_json)

    assert result["status"] == "SUCCESS"
    assert result["actor_id"] == 42
    assert result["signal"] == "vehicle.speed"
    assert result["sample_count"] == 2
    assert [sample["value"] for sample in result["samples"]] == [
        36.0,
        42.0,
    ]