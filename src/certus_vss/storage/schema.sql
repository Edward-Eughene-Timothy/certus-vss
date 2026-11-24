-- TIER 2: 1-Minute Statistical Telemetry Rollups
-- Clustered by (vss_path, bucket_epoch_m) so the table acts as its own signal index
CREATE TABLE IF NOT EXISTS telemetry_rollup_1m (
    vss_path        TEXT NOT NULL,
    bucket_epoch_m  INTEGER NOT NULL,
    val_avg         REAL NOT NULL,
    val_min         REAL NOT NULL,
    val_max         REAL NOT NULL,
    val_delta       REAL NOT NULL,
    sample_count    INTEGER NOT NULL,
    unit            TEXT NOT NULL,
    drive_context   TEXT NOT NULL,
    provenance      TEXT NOT NULL,
    PRIMARY KEY (vss_path, bucket_epoch_m)
) WITHOUT ROWID;

-- TIER 4: Discrete Diagnostic Events
CREATE TABLE IF NOT EXISTS vehicle_events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc_s  INTEGER NOT NULL,
    event_category   TEXT NOT NULL,
    dtc_code         TEXT,
    severity         TEXT NOT NULL,
    trigger_signal   TEXT NOT NULL,
    trigger_value    REAL NOT NULL,
    context_json     TEXT NOT NULL,
    provenance       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_category_time 
    ON vehicle_events (event_category, timestamp_utc_s);

-- TIER 3: Fault Freeze-Frames (10 Hz pre/post fault window)
CREATE TABLE IF NOT EXISTS fault_freeze_frames (
    event_id         INTEGER NOT NULL,
    offset_ms        INTEGER NOT NULL,
    vss_path         TEXT NOT NULL,
    raw_value        REAL NOT NULL,
    PRIMARY KEY (event_id, offset_ms, vss_path),
    FOREIGN KEY(event_id) REFERENCES vehicle_events(event_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_freeze_frames_vss 
    ON fault_freeze_frames (event_id, vss_path);

-- TIER 5: Broad CARLA Acquisition
-- Kept separate from the six-signal diagnostic telemetry pipeline.

CREATE TABLE IF NOT EXISTS acquisition_samples (
    sample_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc_s  REAL NOT NULL,
    simulation_time_s REAL,
    actor_id         INTEGER,
    signal           TEXT NOT NULL,
    value_json       TEXT,
    unit             TEXT NOT NULL,
    source           TEXT NOT NULL,
    provenance       TEXT NOT NULL,
    availability     TEXT NOT NULL,
    created_at_utc_s REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_acquisition_samples_signal_time
    ON acquisition_samples (signal, timestamp_utc_s);

CREATE INDEX IF NOT EXISTS idx_acquisition_samples_actor_time
    ON acquisition_samples (actor_id, timestamp_utc_s);


CREATE TABLE IF NOT EXISTS acquisition_events (
    event_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc_s   REAL NOT NULL,
    simulation_time_s REAL,
    actor_id          INTEGER,
    event_type        TEXT NOT NULL,
    event_json        TEXT,
    source            TEXT NOT NULL,
    provenance        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_acquisition_events_type_time
    ON acquisition_events (event_type, timestamp_utc_s);


CREATE TABLE IF NOT EXISTS acquisition_sensor_frames (
    frame_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc_s    REAL NOT NULL,
    simulation_time_s  REAL,
    actor_id           INTEGER,
    sensor_type        TEXT NOT NULL,
    payload_reference TEXT NOT NULL,
    metadata_json      TEXT,
    source             TEXT NOT NULL,
    provenance         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_acquisition_sensor_time
    ON acquisition_sensor_frames (sensor_type, timestamp_utc_s);
