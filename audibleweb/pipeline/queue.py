"""Job audio artifact helpers: directory convention + cleanup on delete/fail."""

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def job_audio_dir(data_dir: str | Path, job_id: str) -> Path:
    """Per-job directory where TTS chunk WAVs are written during synthesis."""
    return Path(data_dir) / "jobs" / job_id


def cleanup_job_audio(data_dir: str | Path, job_id: str) -> None:
    """Remove the job's audio chunk directory if it exists."""
    d = job_audio_dir(data_dir, job_id)
    if d.exists():
        shutil.rmtree(d)


def fail_job(
    conn: sqlite3.Connection, job_id: str, error: str, data_dir: str | Path
) -> None:
    """Mark job as failed in DB and remove its audio chunk directory."""
    conn.execute(
        "UPDATE jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
        (error, datetime.now(UTC).isoformat(), job_id),
    )
    conn.commit()
    cleanup_job_audio(data_dir, job_id)
