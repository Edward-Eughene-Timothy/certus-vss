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
