-- Persisted per-job step log: one row per meaningful pipeline step (stage
-- text, retry/failure tally, ETA), shown as a collapsible timeline per job
-- card. Cascade-deletes with its job, same as `chunks`.
CREATE TABLE job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_job_events_job ON job_events(job_id, created_at);
