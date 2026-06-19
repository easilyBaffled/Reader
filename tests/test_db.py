from audibleweb.db import get_connection, migrate


def test_migrate_creates_schema(tmp_path):
    conn = get_connection(tmp_path / "test.db")

    version = migrate(conn)

    assert version == 6
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"jobs", "chunks", "rss_seen_items", "job_events"} <= tables


def test_migrate_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "test.db")

    migrate(conn)
    version = migrate(conn)

    assert version == 6
    # second run must not error re-creating tables
    conn.execute("SELECT * FROM jobs")
    conn.execute("SELECT * FROM chunks")


def test_job_events_cascade_deletes_with_job(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    now = "2026-06-19T00:00:00+00:00"
    conn.execute(
        "INSERT INTO jobs (id, status, input_type, input_value, created_at, updated_at)"
        " VALUES ('job-1', 'queued', 'raw_text', 'hello', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, stage, detail, created_at)"
        " VALUES ('job-1', 'extracting', 'Reading pasted text', ?)",
        (now,),
    )
    conn.commit()

    conn.execute("DELETE FROM jobs WHERE id = 'job-1'")
    conn.commit()

    rows = conn.execute("SELECT * FROM job_events WHERE job_id = 'job-1'").fetchall()
    assert rows == []
