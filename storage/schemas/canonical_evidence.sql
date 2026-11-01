CREATE TABLE IF NOT EXISTS immutable_evidence_log (
    evidence_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    speaker TEXT NOT NULL DEFAULT 'UNKNOWN',
    text TEXT NOT NULL,
    start_timestamp REAL NOT NULL,
    end_timestamp REAL,
    is_final INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_evidence_meeting 
    ON immutable_evidence_log(meeting_id);
CREATE INDEX IF NOT EXISTS idx_evidence_ts 
    ON immutable_evidence_log(start_timestamp);

CREATE TABLE IF NOT EXISTS durable_work_queue (
    event_id TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED', 'DEAD_LETTER')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    checkpoint TEXT,
    locked_at REAL,
    locked_by TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(evidence_id) REFERENCES immutable_evidence_log(evidence_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_work_queue_state 
    ON durable_work_queue(state, locked_at);
