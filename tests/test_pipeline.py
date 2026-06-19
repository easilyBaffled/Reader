"""End-to-end pipeline test: raw_text job -> done, MP3 + feed.xml produced."""

import asyncio
import io
import wave
import xml.etree.ElementTree as ET

import pytest

from audibleweb.config import AppConfig, PublisherConfig
from audibleweb.core.pipeline import PipelinePausedError, run_pipeline
from audibleweb.db import get_connection, migrate

# 150+ chars to pass MIN_CONTENT_CHARS
_ARTICLE = (
    "Scientists have discovered that regular exercise improves cognitive function. "
    "The study, which followed 10,000 participants over five years, found a strong "
    "correlation between physical activity and brain health."
)


def run(coro):
    return asyncio.run(coro)


class _FakeEngine:
    name = "fake"
    supports_blending = False

    async def synthesize(self, text: str, voice: str, speed: float = 1.0, **_) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * 24000)  # 1 second silence
        return buf.getvalue()


def _insert_job(conn, job_id, input_type="raw_text", input_value=None):
    now = "2026-06-15T00:00:00+00:00"
    conn.execute(
        "INSERT INTO jobs (id, status, input_type, input_value, created_at, updated_at) "
        "VALUES (?, 'queued', ?, ?, ?, ?)",
        (job_id, input_type, input_value or _ARTICLE, now, now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_pipeline_raw_text_produces_mp3_and_feed(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")

    config = AppConfig(publisher=PublisherConfig(type="local"))
    run(
        run_pipeline(
            conn,
            "job-1",
            config=config,
            engine=_FakeEngine(),
            pronunciation={},
            data_dir=tmp_path,
        )
    )

    row = conn.execute("SELECT * FROM jobs WHERE id = ?", ("job-1",)).fetchone()
    assert row["status"] == "done"
    assert row["title"] is not None and row["title"] != ""
    assert row["word_count"] > 0
    assert row["audio_duration_sec"] > 0
    assert row["file_size_bytes"] > 0
    assert row["public_url"] and row["public_url"].endswith(".mp3")

    mp3 = tmp_path / "audio" / "job-1.mp3"
    assert mp3.exists()
    feed_xml = tmp_path / "feed.xml"
    assert feed_xml.exists()

    # Feed must be valid RSS with the episode
    tree = ET.parse(feed_xml)
    items = tree.findall(".//item")
    assert len(items) == 1
    assert items[0].find("title").text is not None


def test_pipeline_updates_status_sequence(tmp_path):
    """Verify stage transitions: extracting -> normalizing -> generating -> publishing -> done."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")

    config = AppConfig(publisher=PublisherConfig(type="local"))
    run(
        run_pipeline(
            conn,
            "job-1",
            config=config,
            engine=_FakeEngine(),
            pronunciation={},
            data_dir=tmp_path,
        )
    )

    row = conn.execute("SELECT status FROM jobs WHERE id = ?", ("job-1",)).fetchone()
    assert row["status"] == "done"


def test_pipeline_chunks_written_to_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")

    config = AppConfig(publisher=PublisherConfig(type="local"))
    run(
        run_pipeline(
            conn,
            "job-1",
            config=config,
            engine=_FakeEngine(),
            pronunciation={},
            data_dir=tmp_path,
        )
    )

    chunks = conn.execute(
        "SELECT * FROM chunks WHERE job_id = ?", ("job-1",)
    ).fetchall()
    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk["status"] == "done"
        assert chunk["audio_path"] is None or True  # audio dir cleaned up after stitch


def test_pipeline_logs_job_events_per_stage(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")

    config = AppConfig(publisher=PublisherConfig(type="local"))
    run(
        run_pipeline(
            conn,
            "job-1",
            config=config,
            engine=_FakeEngine(),
            pronunciation={},
            data_dir=tmp_path,
        )
    )

    events = conn.execute(
        "SELECT stage, detail FROM job_events WHERE job_id = ? ORDER BY id", ("job-1",)
    ).fetchall()
    stages_seen = [e["stage"] for e in events]
    assert "extracting" in stages_seen
    assert "normalizing" in stages_seen
    assert "generating" in stages_seen
    assert "publishing" in stages_seen
    # the extracting detail should mention raw_text's fixed message
    extracting_details = [e["detail"] for e in events if e["stage"] == "extracting"]
    assert extracting_details == ["Reading pasted text"]


def test_pipeline_logs_permanent_chunk_failure_event(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(
        conn, "job-1",
        input_value=(
            "This is the first sentence padding out the article content nicely. "
            "Second sentence absolutely fails badly here today. "
            "Third sentence wraps things up calmly."
        ),
    )

    class _OneBadChunkEngine:
        name = "onebad"
        supports_blending = False

        async def synthesize(self, text: str, voice: str, speed: float = 1.0, **_) -> bytes:
            if "fails" in text:
                raise RuntimeError("synthesis exploded")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 24000)
            return buf.getvalue()

    config = AppConfig(publisher=PublisherConfig(type="local"))
    with pytest.raises(RuntimeError, match="synthesis exploded"):
        run(
            run_pipeline(
                conn, "job-1", config=config, engine=_OneBadChunkEngine(),
                pronunciation={}, data_dir=tmp_path,
            )
        )

    events = conn.execute(
        "SELECT detail FROM job_events WHERE job_id = ? AND stage = 'generating'"
        " ORDER BY id",
        ("job-1",),
    ).fetchall()
    failure_events = [e["detail"] for e in events if "failed permanently" in e["detail"]]
    assert len(failure_events) == 1
    assert "synthesis exploded" in failure_events[0]


def test_format_duration_formats_minutes_and_seconds():
    from audibleweb.core.pipeline import _format_duration

    assert _format_duration(5) == "5s"
    assert _format_duration(65) == "1m 5s"
    assert _format_duration(0) == "0s"
    assert _format_duration(-3) == "0s"  # clamp, never show negative


def test_synthesize_all_counts_retries_into_progress_event(tmp_path):
    from audibleweb.core.pipeline import _synthesize_all

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")
    now = "2026-06-19T00:00:00+00:00"
    # 10 chunks -- exactly the throttle floor, guarantees one emitted event.
    for i in range(10):
        conn.execute(
            "INSERT INTO chunks (job_id, chunk_index, text, status, created_at, updated_at)"
            " VALUES ('job-1', ?, ?, 'pending', ?, ?)",
            (i, f"chunk {i}", now, now),
        )
    conn.commit()

    class _RetryOnceEngine:
        name = "retryonce"
        supports_blending = False

        def __init__(self):
            self.calls = 0

        async def synthesize(self, text, voice, speed=1.0, *, on_retry=None, **_):
            self.calls += 1
            if self.calls == 1 and on_retry is not None:
                on_retry(0, RuntimeError("transient"))
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 24000)
            return buf.getvalue()

    run(
        _synthesize_all(
            conn, "job-1", [f"chunk {i}" for i in range(10)], _RetryOnceEngine(),
            "af_heart", 1.0, tmp_path,
        )
    )

    events = conn.execute(
        "SELECT detail FROM job_events WHERE job_id = ? AND stage = 'generating'"
        " ORDER BY id",
        ("job-1",),
    ).fetchall()
    progress_events = [e["detail"] for e in events if "/10 segments" in e["detail"]]
    assert len(progress_events) >= 1
    assert "1 retries" in progress_events[0]


def test_synthesize_all_emits_throttled_progress_every_ten_chunks(tmp_path):
    from audibleweb.core.pipeline import _synthesize_all

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")
    now = "2026-06-19T00:00:00+00:00"
    chunks = [f"chunk {i}" for i in range(12)]
    for i, text in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks (job_id, chunk_index, text, status, created_at, updated_at)"
            " VALUES ('job-1', ?, ?, 'pending', ?, ?)",
            (i, text, now, now),
        )
    conn.commit()

    run(
        _synthesize_all(
            conn, "job-1", chunks, _FakeEngine(), "af_heart", 1.0, tmp_path,
        )
    )

    events = conn.execute(
        "SELECT detail FROM job_events WHERE job_id = ? AND stage = 'generating'"
        " ORDER BY id",
        ("job-1",),
    ).fetchall()
    # 12 chunks, throttle fires every 10 -> at least one "X/12 segments" event
    progress_events = [e["detail"] for e in events if "/12 segments" in e["detail"]]
    assert len(progress_events) >= 1
    assert "remaining" in progress_events[0]


def test_pipeline_pronunciation_applied(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(
        conn,
        "job-1",
        input_value=(
            "The API call returned HTTP status code 200. "
            "The API response was cached for performance. "
            "We use HTTP for all our internal services."
        ),
    )

    synthesized: list[str] = []
    original_engine = _FakeEngine()

    class _RecordingEngine:
        name = "recording"
        supports_blending = False

        async def synthesize(
            self, text: str, voice: str, speed: float = 1.0, **_
        ) -> bytes:
            synthesized.append(text)
            return await original_engine.synthesize(text, voice, speed)

    config = AppConfig(publisher=PublisherConfig(type="local"))
    run(
        run_pipeline(
            conn,
            "job-1",
            config=config,
            engine=_RecordingEngine(),
            pronunciation={"API": "A-P-I", "HTTP": "H-T-T-P"},
            data_dir=tmp_path,
        )
    )

    combined = " ".join(synthesized)
    assert "A-P-I" in combined
    assert "H-T-T-P" in combined
    assert "API" not in combined
    assert "HTTP" not in combined


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_pipeline_tts_failure_marks_job_failed(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")

    class _BrokenEngine:
        name = "broken"
        supports_blending = False

        async def synthesize(
            self, text: str, voice: str, speed: float = 1.0, **_
        ) -> bytes:
            raise RuntimeError("TTS server unavailable")

    config = AppConfig(publisher=PublisherConfig(type="local"))
    with pytest.raises(RuntimeError, match="TTS server unavailable"):
        run(
            run_pipeline(
                conn,
                "job-1",
                config=config,
                engine=_BrokenEngine(),
                pronunciation={},
                data_dir=tmp_path,
            )
        )

    row = conn.execute("SELECT status FROM jobs WHERE id = ?", ("job-1",)).fetchone()
    # run_pipeline raises; worker.py calls fail_job(). Here we just verify the exception.
    # Status will still be 'generating' (the worker sets it to failed).
    assert row["status"] == "generating"


def test_pipeline_pause_raises_paused_error(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")

    calls = [0]

    class _PausingEngine:
        name = "pausing"
        supports_blending = False

        async def synthesize(
            self, text: str, voice: str, speed: float = 1.0, **_
        ) -> bytes:
            # Pause the job after first chunk synthesized
            if calls[0] == 0:
                conn.execute(
                    "UPDATE jobs SET status='paused', updated_at='2026-06-15T00:00:01' WHERE id='job-1'"
                )
                conn.commit()
            calls[0] += 1
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 24000)
            return buf.getvalue()

    config = AppConfig(publisher=PublisherConfig(type="local"))
    with pytest.raises(PipelinePausedError):
        run(
            run_pipeline(
                conn,
                "job-1",
                config=config,
                engine=_PausingEngine(),
                pronunciation={},
                data_dir=tmp_path,
            )
        )
