import asyncio
import io
import time
import wave

from audibleweb.config import AppConfig, ExtractionConfig, PublisherConfig
from audibleweb.db import get_connection, migrate
from audibleweb.extractors.base import Article, ExtractionError
from audibleweb.pipeline.queue import fail_job
from audibleweb.worker import Worker, _poll_rss_feeds

# 150+ chars to pass MIN_CONTENT_CHARS check in extractors/base.py
_LONG_TEXT = (
    "The quick brown fox jumps over the lazy dog. "
    "Pack my box with five dozen liquor jugs. "
    "How vexingly quick daft zebras jump!"
)


class _FakeRSSExtractor:
    """Returns canned articles per feed_url; a feed_url mapped to an
    Exception instance raises it instead (simulates an unreachable feed)."""

    def __init__(self, by_feed: dict[str, list[Article] | Exception]):
        self._by_feed = by_feed

    async def list_new_articles(self, feed_url, conn):
        result = self._by_feed.get(feed_url, [])
        if isinstance(result, Exception):
            raise result
        return result


def _make_article(source_url: str | None, title: str = "Test") -> Article:
    return Article(
        title=title,
        text=_LONG_TEXT,
        source_url=source_url,
        author=None,
        published=None,
        word_count=10,
    )


class _FakeEngine:
    """Returns a minimal valid WAV without hitting a real TTS server."""

    name = "fake"
    supports_blending = False

    async def synthesize(self, text: str, voice: str, speed: float = 1.0, **_) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 24000)  # 1 second of silence
        return buf.getvalue()

    async def list_voices(self) -> list[str]:
        return ["af_heart"]


def _make_config(tmp_path) -> AppConfig:
    return AppConfig(publisher=PublisherConfig(type="local"))


def _insert_job(conn, job_id, status="queued"):
    now = "2026-06-15T00:00:00+00:00"
    conn.execute(
        "INSERT INTO jobs (id, status, input_type, input_value, created_at, updated_at) "
        "VALUES (?, ?, 'raw_text', ?, ?, ?)",
        (job_id, status, _LONG_TEXT, now, now),
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


def test_poll_rss_feeds_creates_job_from_new_article(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)

    config = AppConfig(
        extraction=ExtractionConfig(rss_feeds=["http://example.com/rss"])
    )
    extractor = _FakeRSSExtractor(
        {"http://example.com/rss": [_make_article("http://example.com/a1")]}
    )

    asyncio.run(_poll_rss_feeds(conn, config, extractor=extractor))

    rows = conn.execute("SELECT input_type, input_value, status FROM jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["input_type"] == "url"
    assert rows[0]["input_value"] == "http://example.com/a1"
    assert rows[0]["status"] == "queued"
    conn.close()


def test_poll_rss_feeds_skips_article_without_link(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)

    config = AppConfig(
        extraction=ExtractionConfig(rss_feeds=["http://example.com/rss"])
    )
    extractor = _FakeRSSExtractor({"http://example.com/rss": [_make_article(None)]})

    asyncio.run(_poll_rss_feeds(conn, config, extractor=extractor))

    rows = conn.execute("SELECT * FROM jobs").fetchall()
    assert rows == []
    conn.close()


def test_poll_rss_feeds_continues_after_one_feed_fails(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)

    config = AppConfig(
        extraction=ExtractionConfig(
            rss_feeds=["http://bad.com/rss", "http://good.com/rss"]
        )
    )
    extractor = _FakeRSSExtractor(
        {
            "http://bad.com/rss": ExtractionError("Could not fetch feed: boom"),
            "http://good.com/rss": [_make_article("http://good.com/a1")],
        }
    )

    asyncio.run(_poll_rss_feeds(conn, config, extractor=extractor))

    rows = conn.execute("SELECT input_value FROM jobs").fetchall()
    assert [r["input_value"] for r in rows] == ["http://good.com/a1"]
    conn.close()


def test_worker_picks_up_queued_job(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")
    conn.close()

    worker = Worker(
        db_path,
        poll_interval=0.05,
        config=_make_config(tmp_path),
        engine=_FakeEngine(),
    )
    worker.start()
    try:
        deadline = time.monotonic() + 10
        status = _job_status(db_path, "job-1")
        while status not in ("done", "failed") and time.monotonic() < deadline:
            time.sleep(0.1)
            status = _job_status(db_path, "job-1")

        assert status == "done"
    finally:
        worker.stop()


def test_worker_graceful_shutdown(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()

    worker = Worker(
        db_path,
        poll_interval=0.05,
        config=_make_config(tmp_path),
        engine=_FakeEngine(),
    )
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


def test_maybe_poll_rss_skips_when_not_due(tmp_path, monkeypatch):
    import audibleweb.worker as worker_module

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)

    config = AppConfig(
        extraction=ExtractionConfig(
            rss_feeds=["http://example.com/rss"], rss_poll_interval=3600
        )
    )
    worker = Worker(db_path, config=config, engine=_FakeEngine())
    worker._last_rss_poll = time.monotonic()  # just polled

    calls = []

    async def fake_poll(conn, config, *, extractor=None):
        calls.append(1)

    monkeypatch.setattr(worker_module, "_poll_rss_feeds", fake_poll)

    asyncio.run(worker._maybe_poll_rss(conn, config))

    assert calls == []
    conn.close()


def test_maybe_poll_rss_runs_when_due(tmp_path, monkeypatch):
    import audibleweb.worker as worker_module

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)

    config = AppConfig(
        extraction=ExtractionConfig(
            rss_feeds=["http://example.com/rss"], rss_poll_interval=0
        )
    )
    worker = Worker(db_path, config=config, engine=_FakeEngine())
    worker._last_rss_poll = 0.0

    calls = []

    async def fake_poll(conn, config, *, extractor=None):
        calls.append(1)

    monkeypatch.setattr(worker_module, "_poll_rss_feeds", fake_poll)

    asyncio.run(worker._maybe_poll_rss(conn, config))

    assert calls == [1]
    assert worker._last_rss_poll > 0.0
    conn.close()
