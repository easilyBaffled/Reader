-- Initial schema: job queue + per-chunk TTS progress.

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    input_type TEXT NOT NULL,
    input_value TEXT NOT NULL,
    title TEXT,
    source_url TEXT,
    word_count INTEGER,
    audio_duration_sec REAL,
    audio_path TEXT,
    public_url TEXT,
    error TEXT,
    voice_config TEXT,  -- JSON
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_jobs_status ON jobs(status);

CREATE TABLE chunks (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    audio_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, chunk_index)
);
