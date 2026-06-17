from audibleweb.db import get_connection, migrate


def test_migrate_creates_schema(tmp_path):
    conn = get_connection(tmp_path / "test.db")

    version = migrate(conn)

    assert version == 4
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"jobs", "chunks", "rss_seen_items"} <= tables


def test_migrate_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "test.db")

    migrate(conn)
    version = migrate(conn)

    assert version == 4
    # second run must not error re-creating tables
    conn.execute("SELECT * FROM jobs")
    conn.execute("SELECT * FROM chunks")
