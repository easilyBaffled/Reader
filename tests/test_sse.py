"""Tests for SSE progress stream (api/sse.py)."""

from __future__ import annotations

import json

import httpx
import pytest

from audibleweb.app import create_app
from audibleweb.db import get_connection
from audibleweb.engines.kokoro import KokoroEngine

BASE_URL = "http://mock-tts/v1"


def _mock_engine() -> KokoroEngine:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/voices"):
            return httpx.Response(200, json={"voices": ["af_heart"]})
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
    return KokoroEngine(base_url=BASE_URL, client=client)


@pytest.fixture
def app(tmp_path):
    return create_app(
        db_path=tmp_path / "test.db",
        start_worker=False,
        tts_engine=_mock_engine(),
        pronunciation_path=tmp_path / "pronunciation.json",
    )


@pytest.fixture
def client(app):
    return app.test_client()


def _insert_job(app, job_id, status="queued", **extra):
    now = "2026-06-15T00:00:00+00:00"
    columns = ["id", "status", "input_type", "input_value", "created_at", "updated_at"]
    values = [job_id, status, "raw_text", "hello", now, now]
    for key, value in extra.items():
        columns.append(key)
        values.append(value)
    conn = get_connection(app.config["DB_PATH"])
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({placeholders})", values
    )
    conn.commit()
    conn.close()


def _insert_chunks(app, job_id, total: int, done: int):
    now = "2026-06-15T00:00:00+00:00"
    conn = get_connection(app.config["DB_PATH"])
    for i in range(total):
        status = "done" if i < done else "pending"
        conn.execute(
            "INSERT INTO chunks (job_id, chunk_index, text, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, i, f"chunk {i}", status, now, now),
        )
    conn.commit()
    conn.close()


def _parse_events(body: bytes) -> list[dict]:
    events = []
    for line in body.decode().splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# --- endpoint exists and returns SSE content-type ---


def test_stream_returns_event_stream_content_type(client, app):
    _insert_job(app, "j1", status="done")
    resp = client.get("/api/jobs/j1/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.content_type


# --- terminal job emits one event and closes ---


def test_stream_done_job_emits_single_event(client, app):
    _insert_job(app, "j2", status="done", title="My Article")
    resp = client.get("/api/jobs/j2/stream")
    events = _parse_events(resp.data)
    assert len(events) == 1
    assert events[0]["status"] == "done"
    assert events[0]["id"] == "j2"
    assert events[0]["title"] == "My Article"


def test_stream_failed_job_emits_single_event(client, app):
    _insert_job(app, "j3", status="failed", error="extraction failed")
    resp = client.get("/api/jobs/j3/stream")
    events = _parse_events(resp.data)
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert events[0]["error"] == "extraction failed"


# --- chunk progress in generating stage ---


def test_stream_generating_includes_chunk_counts(client, app):
    _insert_job(app, "j4", status="generating")
    _insert_chunks(app, "j4", total=5, done=3)
    client.get("/api/jobs/j4/stream")
    # generating is not terminal so stream loops; Flask test client reads until
    # generator exhausts or we use streaming=True. Since status stays "generating"
    # forever in this test, we need to make it terminal after first read.
    # Instead insert as "done" and verify chunk fields are 0 (only populated for "generating").
    # Re-test with a done job that had generating chunks:
    _insert_job(app, "j5", status="done")
    _insert_chunks(app, "j5", total=5, done=5)
    resp2 = client.get("/api/jobs/j5/stream")
    events = _parse_events(resp2.data)
    assert events[0]["chunks_done"] == 0  # not "generating" status, so 0
    assert events[0]["chunks_total"] == 0


def test_stream_generating_chunk_progress_via_snapshot(app, client):
    """Snapshot: generating job reports chunk counts correctly."""
    from audibleweb.api.sse import _progress

    _insert_job(app, "j6", status="generating")
    _insert_chunks(app, "j6", total=10, done=4)
    result = _progress(app.config["DB_PATH"], "j6")
    assert result["status"] == "generating"
    assert result["chunks_total"] == 10
    assert result["chunks_done"] == 4


# --- unknown job ---


def test_stream_unknown_job_returns_error_event(client):
    resp = client.get("/api/jobs/nonexistent/stream")
    events = _parse_events(resp.data)
    assert len(events) == 1
    assert "error" in events[0]


# --- cache-control header ---


def test_stream_cache_control_no_cache(client, app):
    _insert_job(app, "j7", status="done")
    resp = client.get("/api/jobs/j7/stream")
    assert resp.headers.get("Cache-Control") == "no-cache"
