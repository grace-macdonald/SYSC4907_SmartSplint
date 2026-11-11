-- Smart Splint web app schema (SQLite).
--
-- Privacy rules from proposal section 1.12 / NFR-13:
--   * No names are stored anywhere. Participants are identified only by a
--     participant ID (e.g. "P-001"). The ID -> name mapping is held by the
--     study team outside this system.
--   * No sensor data is accepted for a participant until consent is recorded
--     (FR-12). Withdrawing consent deletes all of the participant's data.
--   * Session data older than the retention period (12 months) is purged.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS participants (
    id                       TEXT PRIMARY KEY,          -- participant ID, e.g. "P-001"
    created_at               TEXT NOT NULL,
    body_mass_kg             REAL,                      -- entered by the research team (FR-6)
    support_level_pct        INTEGER NOT NULL DEFAULT 0, -- 0..100 in 5% steps (FR-6)
    support_max_pct          INTEGER NOT NULL DEFAULT 0, -- clinician-set ceiling for the level
    weight_bearing_limit_kg  REAL,                      -- clinician-set plantar load limit (1.11)
    rom_plantar_limit_deg    REAL NOT NULL DEFAULT 25,  -- NFR-3 default hard stop
    rom_dorsi_limit_deg      REAL NOT NULL DEFAULT 15,  -- NFR-3 default hard stop
    consent_given_at         TEXT,                      -- NULL = no consent, no data stored
    consent_version          TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,   -- clinicians: a login name; participants: their participant ID
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('clinician', 'participant')),
    participant_id  TEXT REFERENCES participants(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    CHECK ((role = 'participant') = (participant_id IS NOT NULL))
);

-- A brace (or the phone relaying for it) authenticates to the ingest API with
-- a per-device token. Only a hash of the token is stored.
CREATE TABLE IF NOT EXISTS devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id  TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    label           TEXT,
    token_hash      TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    revoked_at      TEXT
);

-- One walking (or calibration) session. Summary metrics are computed on the
-- brace / in the cloud pipeline and sent with the session; they drive the
-- progress charts so the dashboards never need to scan raw samples.
CREATE TABLE IF NOT EXISTS sessions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id           TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    kind                     TEXT NOT NULL DEFAULT 'walk' CHECK (kind IN ('walk', 'calibration', 'bench')),
    started_at               TEXT NOT NULL,
    ended_at                 TEXT,
    support_level_pct        INTEGER,
    reference_torque_nm      REAL,      -- peak push-off torque requested at that level
    strides                  INTEGER,
    cadence_spm              REAL,      -- steps per minute
    stride_time_mean_s       REAL,
    stride_time_cv_pct       REAL,      -- stride-to-stride variation (P9)
    peak_plantar_load_kg     REAL,
    push_off_torque_nm       REAL,      -- mean measured peak hinge torque at push-off
    emg_peak_norm            REAL,      -- calf EMG envelope peak, normalised to calibration
    rom_deg                  REAL,      -- ankle range of motion used during the session
    assist_active_pct        REAL,      -- share of strides where the model was confident enough to assist (FR-5)
    model_confidence_mean    REAL,
    notes                    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_participant ON sessions(participant_id, started_at);

-- Time-synchronised samples on a common clock (FR-2, NFR-11). NULL in a
-- sensor column marks a missing reading; valid = 0 marks an invalid frame.
CREATE TABLE IF NOT EXISTS samples (
    session_id        INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    t_ms              INTEGER NOT NULL,
    fsr_heel          REAL,
    fsr_medial        REAL,
    fsr_lateral       REAL,
    fsr_toe           REAL,
    emg               REAL,
    imu_foot_pitch    REAL,
    imu_foot_gyro     REAL,
    imu_shank_pitch   REAL,
    imu_shank_gyro    REAL,
    ankle_angle_deg   REAL,
    hinge_torque_nm   REAL,
    gait_phase        TEXT CHECK (gait_phase IN ('stance', 'push_off', 'swing') OR gait_phase IS NULL),
    phase_confidence  REAL,
    valid             INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (session_id, t_ms)
) WITHOUT ROWID;

-- Gait anomaly alerts (P9), safe-state entries (FR-11), e-stops, and
-- configuration changes (an audit trail of who changed support levels).
CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id   TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    session_id       INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    occurred_at      TEXT NOT NULL,
    received_at      TEXT NOT NULL,
    category         TEXT NOT NULL CHECK (category IN ('anomaly', 'safety', 'config')),
    type             TEXT NOT NULL,
    severity         TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    detail           TEXT,            -- JSON
    acknowledged_at  TEXT,
    acknowledged_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_participant ON events(participant_id, occurred_at);
