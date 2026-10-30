-- ============================================================================
-- CertusVSS Storage Schema
-- Covers Tier 2 (Warm Rollups), Tier 3 (Freeze-Frames), & Tier 4 (Event Logs)
-- (Tier 1 is entirely in RAM to prevent flash burnout)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- TIER 2: 1-Minute Statistical Telemetry Rollups (30+ Day Retention)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry_rollup_1m (
    bucket_epoch_m  INTEGER NOT NULL,    -- Epoch minute (time_s // 60)
    vss_path        TEXT NOT NULL,       -- COVESA standard signal path
    val_avg         REAL NOT NULL,       -- 60s mathematical average
    val_min         REAL NOT NULL,       -- Minimum value in the 60s window
    val_max         REAL NOT NULL,       -- Maximum value in the 60s window
    val_delta       REAL NOT NULL,       -- Last valid sample minus first valid sample
    sample_count    INTEGER NOT NULL,    -- Number of raw frames aggregated (~600 at 10Hz)
    unit            TEXT NOT NULL,       -- Engineering unit ('km/h', 'celsius', 'amperes')
    drive_context   TEXT NOT NULL,       -- 'URBAN_STOP_GO', 'HIGHWAY_CRUISE', 'IDLE', 'CHARGING'
    provenance      TEXT NOT NULL,       -- 'SIMULATED', 'REPLAYED', 'REAL'
    PRIMARY KEY (bucket_epoch_m, vss_path)
) WITHOUT ROWID;

-- Fast index for comparing cohorts (e.g. City vs Highway energy draw over weeks)
CREATE INDEX IF NOT EXISTS idx_rollup_context_time 
    ON telemetry_rollup_1m (drive_context, bucket_epoch_m);

-- ----------------------------------------------------------------------------
-- TIER 4: Discrete Diagnostic Trouble Codes & Vehicle Events
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vehicle_events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc_s  INTEGER NOT NULL,   -- Epoch second of occurrence
    event_category   TEXT NOT NULL,      -- 'THERMAL_WARN', 'DTC_ALERT', 'ADAS_DISENGAGE'
    dtc_code         TEXT,               -- SAE/ISO code (e.g. 'P0A7F', 'C0040')
    severity         TEXT NOT NULL,      -- 'INFO', 'WARNING', 'CRITICAL'
    trigger_signal   TEXT NOT NULL,      -- COVESA path that tripped the alert
    trigger_value    REAL NOT NULL,      -- Numeric sensor reading at trigger
    context_json     TEXT NOT NULL,      -- Snapshot of surrounding vehicle metrics
    provenance       TEXT NOT NULL
);

-- Fast index for scanning events by type and time
CREATE INDEX IF NOT EXISTS idx_events_category_time 
    ON vehicle_events (event_category, timestamp_utc_s);

-- ----------------------------------------------------------------------------
-- TIER 3: High-Resolution Fault Freeze-Frames (On-Trigger Only)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fault_freeze_frames (
    event_id         INTEGER NOT NULL,
    offset_ms        INTEGER NOT NULL,   -- Offset from fault trigger (-120000 to +120000 ms)
    vss_path         TEXT NOT NULL,      -- Sensor path
    raw_value        REAL NOT NULL,      -- 10Hz uncompressed physical reading
    PRIMARY KEY (event_id, offset_ms, vss_path),
    FOREIGN KEY(event_id) REFERENCES vehicle_events(event_id) ON DELETE CASCADE
) WITHOUT ROWID;

-- Fast index to extract a single signal curve across a freeze-frame incident
CREATE INDEX IF NOT EXISTS idx_freeze_frames_vss 
    ON fault_freeze_frames (event_id, vss_path);