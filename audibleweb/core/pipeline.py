"""Pipeline orchestration: extract -> normalize -> generate -> publish.

Stub for now (reader-z4v): just transitions a queued job to done so the
worker harness can be built and tested ahead of the real pipeline stages,
which land in later build tasks (reader-8f2.10 and friends).
"""

import sqlite3
from datetime import UTC, datetime


async def run_pipeline(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', updated_at = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), job_id),
    )
    conn.commit()
