import time

from audibleweb.db import get_connection, migrate
from audibleweb.pipeline.queue import fail_job
from audibleweb.worker import Worker


def _insert_job(conn, job_id, status="queued"):
    now = "2026-06-15T00:00:00+00:00"
    conn.execute(
        "INSERT INTO jobs (id, status, input_type, input_value, created_at, updated_at) "
        "VALUES (?, ?, 'raw_text', 'hello world', ?, ?)",
        (job_id, status, now, now),
    )
    conn.commit()


def _job_status(db_path, job_id):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()["status"]
    finally:
        conn.close()


def test_worker_picks_up_queued_job(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")
    conn.close()

    worker = Worker(db_path, poll_interval=0.05)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        status = _job_status(db_path, "job-1")
        while status != "done" and time.monotonic() < deadline:
            time.sleep(0.05)
            status = _job_status(db_path, "job-1")

        assert status == "done"
    finally:
        worker.stop()


def test_worker_graceful_shutdown(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    worker = Worker(db_path, poll_interval=0.05)
    worker.start()
    worker.stop()

    assert not worker._thread.is_alive()


def test_fail_job_removes_chunk_dir(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")

    chunk_dir = tmp_path / "jobs" / "job-1"
    chunk_dir.mkdir(parents=True)
    (chunk_dir / "chunk_000.wav").write_bytes(b"fake wav")

    fail_job(conn, "job-1", "synthesis error", tmp_path)

    assert not chunk_dir.exists()
    row = conn.execute(
        "SELECT status, error FROM jobs WHERE id = ?", ("job-1",)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "synthesis error"
