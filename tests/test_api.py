from datetime import UTC, datetime, timedelta

import httpx
import pytest
import yaml

from audibleweb.app import create_app
from audibleweb.db import get_connection
from audibleweb.engines.kokoro import KokoroEngine
from audibleweb.extractors.base import ExtractionError

BASE_URL = "http://mock-tts/v1"


def _mock_voices_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/audio/voices"):
        return httpx.Response(200, json={"voices": ["af_heart", "af_bella"]})
    return httpx.Response(404)


def _mock_engine() -> KokoroEngine:
    transport = httpx.MockTransport(_mock_voices_handler)
    client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
    return KokoroEngine(base_url=BASE_URL, client=client)


def _unreachable_engine() -> KokoroEngine:
    return KokoroEngine(base_url="http://127.0.0.1:1/v1")


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
    values = [job_id, status, "raw_text", "hello world", now, now]
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


# --- POST /api/jobs ------------------------------------------------------------


def test_create_job(client):
    resp = client.post("/api/jobs", json={"input": "Hello world", "type": "raw_text"})

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["input_type"] == "raw_text"
    assert body["input_value"] == "Hello world"
    assert body["voice_config"] is None
    assert body["id"]


def test_create_job_with_voice_config(client):
    resp = client.post(
        "/api/jobs",
        json={
            "input": "Hello world",
            "type": "raw_text",
            "voice_config": {"voice": "af_heart", "speed": 1.2},
        },
    )

    assert resp.status_code == 201
    assert resp.get_json()["voice_config"] == {"voice": "af_heart", "speed": 1.2}


def test_create_job_missing_input(client):
    resp = client.post("/api/jobs", json={"type": "raw_text"})

    assert resp.status_code == 400


def test_create_job_invalid_type(client):
    resp = client.post("/api/jobs", json={"input": "Hello world", "type": "bogus"})

    assert resp.status_code == 400


def test_create_job_invalid_voice_spec(client):
    resp = client.post(
        "/api/jobs",
        json={
            "input": "Hello world",
            "type": "raw_text",
            "voice_config": {"voice": "???"},
        },
    )

    assert resp.status_code == 400


def test_create_job_invalid_speed(client):
    resp = client.post(
        "/api/jobs",
        json={
            "input": "Hello world",
            "type": "raw_text",
            "voice_config": {"speed": 5.0},
        },
    )

    assert resp.status_code == 400


# --- GET /api/jobs --------------------------------------------------------------


def test_list_jobs_empty(client):
    resp = client.get("/api/jobs")

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_list_jobs_returns_created(app, client):
    _insert_job(app, "job-1")

    resp = client.get("/api/jobs")

    assert resp.status_code == 200
    body = resp.get_json()
    assert [job["id"] for job in body] == ["job-1"]


def test_list_jobs_filter_by_status(app, client):
    _insert_job(app, "job-1", status="queued")
    _insert_job(app, "job-2", status="done")

    resp = client.get("/api/jobs?status=done")

    assert resp.status_code == 200
    body = resp.get_json()
    assert [job["id"] for job in body] == ["job-2"]


def test_list_jobs_invalid_status(client):
    resp = client.get("/api/jobs?status=bogus")

    assert resp.status_code == 400


# --- GET /api/jobs/:id -----------------------------------------------------------


def test_get_job(app, client):
    _insert_job(app, "job-1")

    resp = client.get("/api/jobs/job-1")

    assert resp.status_code == 200
    assert resp.get_json()["id"] == "job-1"


def test_get_job_not_found(client):
    resp = client.get("/api/jobs/missing")

    assert resp.status_code == 404


def test_stale_heartbeat_surfaced_as_stalled(app, client):
    stale = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    _insert_job(app, "job-stale", status="generating", heartbeat_at=stale)

    resp = client.get("/api/jobs/job-stale")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "stalled"


def test_fresh_heartbeat_not_stalled(app, client):
    fresh = datetime.now(UTC).isoformat()
    _insert_job(app, "job-fresh", status="generating", heartbeat_at=fresh)

    resp = client.get("/api/jobs/job-fresh")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "generating"


# --- DELETE /api/jobs/:id ---------------------------------------------------------


def test_delete_job(app, client):
    _insert_job(app, "job-1")

    resp = client.delete("/api/jobs/job-1")

    assert resp.status_code == 204
    assert client.get("/api/jobs/job-1").status_code == 404


def test_delete_job_removes_audio_file(app, client, tmp_path):
    audio_path = tmp_path / "job-1.mp3"
    audio_path.write_bytes(b"fake mp3")
    _insert_job(app, "job-1", status="done", audio_path=str(audio_path))

    resp = client.delete("/api/jobs/job-1")

    assert resp.status_code == 204
    assert not audio_path.exists()


def test_delete_job_removes_chunk_dir(app, client):
    data_dir = app.config["DB_PATH"].parent
    chunk_dir = data_dir / "jobs" / "job-1"
    chunk_dir.mkdir(parents=True)
    (chunk_dir / "chunk_000.wav").write_bytes(b"fake wav")
    _insert_job(app, "job-1", status="generating")

    resp = client.delete("/api/jobs/job-1")

    assert resp.status_code == 204
    assert not chunk_dir.exists()


def test_delete_job_not_found(client):
    resp = client.delete("/api/jobs/missing")

    assert resp.status_code == 404


# --- GET /api/jobs/:id/events ----------------------------------------------------


def test_list_job_events_not_found(client):
    resp = client.get("/api/jobs/missing/events")
    assert resp.status_code == 404


def test_list_job_events_empty_for_job_with_no_events(app, client):
    _insert_job(app, "job-1")
    resp = client.get("/api/jobs/job-1/events")
    assert resp.status_code == 200
    assert resp.get_json() == {"events": []}


def test_list_job_events_returns_ordered_list(app, client):
    _insert_job(app, "job-1")
    conn = get_connection(app.config["DB_PATH"])
    conn.execute(
        "INSERT INTO job_events (job_id, stage, detail, created_at) VALUES"
        " ('job-1', 'extracting', 'Reading pasted text', '2026-06-19T00:00:01+00:00'),"
        " ('job-1', 'normalizing', 'Cleaning text', '2026-06-19T00:00:02+00:00')"
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/jobs/job-1/events")

    assert resp.status_code == 200
    events = resp.get_json()["events"]
    assert len(events) == 2
    assert events[0]["stage"] == "extracting"
    assert events[0]["detail"] == "Reading pasted text"
    assert events[1]["stage"] == "normalizing"


# --- POST /api/jobs/:id/retry -----------------------------------------------------


def test_retry_failed_job(app, client):
    _insert_job(app, "job-1", status="failed", error="boom")

    resp = client.post("/api/jobs/job-1/retry")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "queued"
    assert body["error"] is None


def test_retry_queued_job_conflict(app, client):
    _insert_job(app, "job-1", status="queued")

    resp = client.post("/api/jobs/job-1/retry")

    assert resp.status_code == 409


def test_retry_not_found(client):
    resp = client.post("/api/jobs/missing/retry")

    assert resp.status_code == 404


# --- POST /api/jobs/:id/pause ------------------------------------------------------


def test_pause_queued_job(app, client):
    _insert_job(app, "job-1", status="queued")

    resp = client.post("/api/jobs/job-1/pause")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "paused"


def test_pause_generating_job(app, client):
    _insert_job(app, "job-1", status="generating")

    resp = client.post("/api/jobs/job-1/pause")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "paused"


def test_pause_done_job_conflict(app, client):
    _insert_job(app, "job-1", status="done")

    resp = client.post("/api/jobs/job-1/pause")

    assert resp.status_code == 409


def test_pause_not_found(client):
    resp = client.post("/api/jobs/missing/pause")

    assert resp.status_code == 404


# --- POST /api/jobs/:id/resume ------------------------------------------------------


def test_resume_paused_job(app, client):
    _insert_job(app, "job-1", status="paused")

    resp = client.post("/api/jobs/job-1/resume")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "queued"


def test_resume_queued_job_conflict(app, client):
    _insert_job(app, "job-1", status="queued")

    resp = client.post("/api/jobs/job-1/resume")

    assert resp.status_code == 409


def test_resume_not_found(client):
    resp = client.post("/api/jobs/missing/resume")

    assert resp.status_code == 404


# --- GET /api/voices -------------------------------------------------------------


def test_list_voices(client):
    resp = client.get("/api/voices")

    assert resp.status_code == 200
    assert resp.get_json() == {"voices": ["af_heart", "af_bella"]}


def test_list_voices_unreachable(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db",
        start_worker=False,
        tts_engine=_unreachable_engine(),
        pronunciation_path=tmp_path / "pronunciation.json",
    )
    client = app.test_client()

    resp = client.get("/api/voices")

    assert resp.status_code == 502


# --- /api/pronunciations -----------------------------------------------------------


def test_pronunciations_empty(client):
    resp = client.get("/api/pronunciations")

    assert resp.status_code == 200
    assert resp.get_json() == {}


def test_pronunciations_put_and_get(client):
    put_resp = client.put(
        "/api/pronunciations",
        json={"word": "Kubernetes", "replacement": "Koo-ber-net-eez"},
    )

    assert put_resp.status_code == 200
    assert put_resp.get_json() == {"Kubernetes": "Koo-ber-net-eez"}

    get_resp = client.get("/api/pronunciations")
    assert get_resp.get_json() == {"Kubernetes": "Koo-ber-net-eez"}


def test_pronunciations_put_missing_fields(client):
    resp = client.put("/api/pronunciations", json={"word": "Kubernetes"})

    assert resp.status_code == 400


def test_pronunciations_delete(client):
    client.put(
        "/api/pronunciations",
        json={"word": "Kubernetes", "replacement": "Koo-ber-net-eez"},
    )

    resp = client.delete("/api/pronunciations/Kubernetes")

    assert resp.status_code == 204
    assert client.get("/api/pronunciations").get_json() == {}


def test_pronunciations_delete_not_found(client):
    resp = client.delete("/api/pronunciations/missing")

    assert resp.status_code == 404


# --- /api/settings ----------------------------------------------------------------


@pytest.fixture
def settings_app(tmp_path):
    return create_app(
        db_path=tmp_path / "test.db",
        start_worker=False,
        tts_engine=_mock_engine(),
        pronunciation_path=tmp_path / "pronunciation.json",
        config_path=tmp_path / "config.yaml",
    )


def test_get_settings_returns_all_sections(settings_app):
    resp = settings_app.test_client().get("/api/settings")

    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {
        "feed",
        "voice",
        "tts",
        "publisher",
        "extraction",
        "normalization",
        "server",
        "logging",
    }


def test_get_settings_omits_secrets(settings_app):
    body = settings_app.test_client().get("/api/settings").get_json()

    assert "token" not in body["publisher"]
    assert "api_key" not in body["tts"]
    assert "jina_api_key" not in body["extraction"]
    assert "llm_api_key" not in body["normalization"]
    assert "api_key" not in body["server"]


def test_put_settings_updates_field(settings_app):
    client = settings_app.test_client()

    resp = client.put("/api/settings", json={"feed": {"title": "New Title"}})

    assert resp.status_code == 200
    assert resp.get_json()["feed"]["title"] == "New Title"


def test_put_settings_writes_yaml(settings_app):
    client = settings_app.test_client()
    client.put("/api/settings", json={"feed": {"title": "Saved"}})

    config_path = settings_app.config["CONFIG_PATH"]
    raw = yaml.safe_load(config_path.read_text())

    assert raw["feed"]["title"] == "Saved"


def test_put_settings_persists_on_get(settings_app):
    client = settings_app.test_client()
    client.put("/api/settings", json={"feed": {"title": "Persisted"}})

    body = client.get("/api/settings").get_json()

    assert body["feed"]["title"] == "Persisted"


def test_put_settings_merges_not_replaces(settings_app):
    client = settings_app.test_client()
    client.put("/api/settings", json={"feed": {"description": "My desc"}})
    client.put("/api/settings", json={"feed": {"title": "My title"}})

    body = client.get("/api/settings").get_json()

    assert body["feed"]["title"] == "My title"
    assert body["feed"]["description"] == "My desc"


def test_put_settings_ignores_secret_fields(settings_app):
    client = settings_app.test_client()

    resp = client.put(
        "/api/settings", json={"publisher": {"token": "secret123", "repo": "user/repo"}}
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert "token" not in body["publisher"]
    assert body["publisher"]["repo"] == "user/repo"

    config_path = settings_app.config["CONFIG_PATH"]
    raw = yaml.safe_load(config_path.read_text())
    assert "token" not in raw.get("publisher", {})


def test_put_settings_invalid_body_not_object(settings_app):
    resp = settings_app.test_client().put("/api/settings", json="not-an-object")

    assert resp.status_code == 400


def test_put_settings_unknown_section(settings_app):
    resp = settings_app.test_client().put(
        "/api/settings", json={"bogus": {"key": "val"}}
    )

    assert resp.status_code == 400


def test_put_settings_invalid_field(settings_app):
    resp = settings_app.test_client().put(
        "/api/settings", json={"feed": {"nonexistent_field": "val"}}
    )

    assert resp.status_code == 400


# --- /api/feeds ----------------------------------------------------------------


def test_get_feeds_empty(settings_app):
    resp = settings_app.test_client().get("/api/feeds")

    assert resp.status_code == 200
    assert resp.get_json() == {"feeds": []}


def test_post_feed_adds_subscribes_and_persists(settings_app, monkeypatch):
    calls = []

    async def fake_first_subscribe(self, url, conn):
        calls.append(url)
        return 5

    monkeypatch.setattr(
        "audibleweb.api.routes.RSSImportExtractor.first_subscribe",
        fake_first_subscribe,
    )

    resp = settings_app.test_client().post(
        "/api/feeds", json={"url": "http://example.com/rss"}
    )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["feeds"] == ["http://example.com/rss"]
    assert body["marked_seen"] == 5
    assert calls == ["http://example.com/rss"]

    config_path = settings_app.config["CONFIG_PATH"]
    raw = yaml.safe_load(config_path.read_text())
    assert raw["extraction"]["rss_feeds"] == ["http://example.com/rss"]


def test_post_feed_missing_url(settings_app):
    resp = settings_app.test_client().post("/api/feeds", json={})
    assert resp.status_code == 400


def test_post_feed_duplicate_rejected(settings_app, monkeypatch):
    async def fake_first_subscribe(self, url, conn):
        return 0

    monkeypatch.setattr(
        "audibleweb.api.routes.RSSImportExtractor.first_subscribe",
        fake_first_subscribe,
    )
    client = settings_app.test_client()
    client.post("/api/feeds", json={"url": "http://example.com/rss"})

    resp = client.post("/api/feeds", json={"url": "http://example.com/rss"})

    assert resp.status_code == 400


def test_post_feed_unreachable_returns_502(settings_app, monkeypatch):
    async def fake_first_subscribe(self, url, conn):
        raise ExtractionError("Could not fetch feed: boom")

    monkeypatch.setattr(
        "audibleweb.api.routes.RSSImportExtractor.first_subscribe",
        fake_first_subscribe,
    )

    resp = settings_app.test_client().post(
        "/api/feeds", json={"url": "http://bad.com/rss"}
    )

    assert resp.status_code == 502
    feeds = settings_app.test_client().get("/api/feeds").get_json()
    assert feeds == {"feeds": []}


def test_delete_feed_removes(settings_app, monkeypatch):
    async def fake_first_subscribe(self, url, conn):
        return 0

    monkeypatch.setattr(
        "audibleweb.api.routes.RSSImportExtractor.first_subscribe",
        fake_first_subscribe,
    )
    client = settings_app.test_client()
    client.post("/api/feeds", json={"url": "http://example.com/rss"})

    resp = client.delete("/api/feeds", json={"url": "http://example.com/rss"})

    assert resp.status_code == 204
    assert client.get("/api/feeds").get_json() == {"feeds": []}


def test_delete_feed_not_found(settings_app):
    resp = settings_app.test_client().delete(
        "/api/feeds", json={"url": "http://nope.com/rss"}
    )
    assert resp.status_code == 404
