-- CertusVSS Persistent Edge Schema
CREATE TABLE IF NOT EXISTS telemetry_rollup_1m (
    bucket_epoch_m  INTEGER NOT NULL,
    vss_path        TEXT NOT NULL,
    val_avg         REAL NOT NULL,
    val_min         REAL NOT NULL,
    val_max         REAL NOT NULL,
    val_delta       REAL NOT NULL,
    sample_count    INTEGER NOT NULL,
    unit            TEXT NOT NULL,
    drive_context   TEXT NOT NULL,
    provenance      TEXT NOT NULL,
    PRIMARY KEY (vss_path, bucket_epoch_m)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_rollup_context_time 
ON telemetry_rollup_1m (drive_context, bucket_epoch_m);

CREATE TABLE IF NOT EXISTS vehicle_events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc_s  INTEGER NOT NULL,
    event_category   TEXT NOT NULL,
    dtc_code         TEXT,
    severity         TEXT NOT NULL,
    trigger_signal   TEXT NOT NULL,
    trigger_value    REAL NOT NULL,
    context_json     TEXT NOT NULL,
    provenance       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_category_time 
ON vehicle_events (event_category, timestamp_utc_s);

CREATE TABLE IF NOT EXISTS fault_freeze_frames (
    event_id         INTEGER NOT NULL,
    offset_ms        INTEGER NOT NULL,
    vss_path         TEXT NOT NULL,
    raw_value        REAL NOT NULL,
    FOREIGN KEY(event_id) REFERENCES vehicle_events(event_id) ON DELETE CASCADE
);
