"""Flask test client: verify each web UI tab renders without error."""

import pytest

from audibleweb.app import create_app
from audibleweb.db import get_connection
from audibleweb.engines.kokoro import KokoroEngine


def _mock_engine() -> KokoroEngine:
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/voices"):
            return httpx.Response(200, json={"voices": ["af_heart"]})
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    client = httpx.AsyncClient(base_url="http://mock-tts/v1", transport=transport)
    return KokoroEngine(base_url="http://mock-tts/v1", client=client)


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


def _insert_job(app, job_id, status="done", **kwargs):
    now = "2026-06-15T00:00:00+00:00"
    cols = ["id", "status", "input_type", "input_value", "created_at", "updated_at"]
    vals = [job_id, status, "url", "https://example.com/article", now, now]
    for k, v in kwargs.items():
        cols.append(k)
        vals.append(v)
    conn = get_connection(app.config["DB_PATH"])
    placeholders = ", ".join("?" for _ in vals)
    conn.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({placeholders})", vals)
    conn.commit()
    conn.close()


class TestRootPage:
    def test_get_root_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_root_contains_tab_bar(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "tab-bar" in html
        assert "Queue" in html
        assert "Inbox" in html
        assert "Feed" in html
        assert "Settings" in html

    def test_root_contains_htmx(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "htmx" in html

    def test_root_contains_tokens_css(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "tokens.css" in html


class TestQueueTab:
    def test_get_tab_queue_returns_200(self, client):
        resp = client.get("/tab/queue")
        assert resp.status_code == 200

    def test_queue_empty_state(self, client):
        resp = client.get("/tab/queue")
        html = resp.data.decode()
        assert "first episode" in html.lower() or "paste a url" in html.lower()

    def test_queue_shows_jobs(self, app, client):
        _insert_job(app, "job-001", status="done", title="Test Article")
        resp = client.get("/tab/queue")
        html = resp.data.decode()
        assert "Test Article" in html

    def test_queue_shows_active_job_with_progress_bar(self, app, client):
        _insert_job(app, "job-gen", status="generating", title="Active Job")
        resp = client.get("/tab/queue")
        html = resp.data.decode()
        assert "progress-track" in html
        assert "Active Job" in html

    def test_queue_shows_status_badge(self, app, client):
        _insert_job(app, "job-fail", status="failed", title="Failed Job")
        resp = client.get("/tab/queue")
        html = resp.data.decode()
        assert "badge-failed" in html

    def test_queue_contains_quick_add_input(self, client):
        resp = client.get("/tab/queue")
        html = resp.data.decode()
        assert "quick-add-input" in html


class TestInboxTab:
    def test_get_tab_inbox_returns_200(self, client):
        resp = client.get("/tab/inbox")
        assert resp.status_code == 200

    def test_inbox_contains_quick_add_input(self, client):
        resp = client.get("/tab/inbox")
        html = resp.data.decode()
        assert "quick-add-input" in html

    def test_inbox_contains_drop_instructions(self, client):
        resp = client.get("/tab/inbox")
        html = resp.data.decode()
        assert "PDF" in html or "drag" in html.lower()


class TestFeedTab:
    def test_get_tab_feed_returns_200(self, client):
        resp = client.get("/tab/feed")
        assert resp.status_code == 200

    def test_feed_empty_state(self, client):
        resp = client.get("/tab/feed")
        html = resp.data.decode()
        assert "no episodes" in html.lower() or "process a job" in html.lower()

    def test_feed_shows_done_episodes(self, app, client):
        _insert_job(
            app,
            "ep-001",
            status="done",
            title="Published Episode",
            public_url="https://example.com/ep.mp3",
        )
        resp = client.get("/tab/feed")
        html = resp.data.decode()
        assert "Published Episode" in html

    def test_feed_url_for_local_publisher(self, tmp_path):
        from audibleweb.app import create_app
        from audibleweb.config import AppConfig, PublisherConfig, ServerConfig

        config = AppConfig(
            publisher=PublisherConfig(type="local"),
            server=ServerConfig(host="127.0.0.1", port=5000),
        )
        app = create_app(
            db_path=tmp_path / "test.db",
            start_worker=False,
            tts_engine=_mock_engine(),
            pronunciation_path=tmp_path / "pronunciation.json",
            config=config,
        )
        _insert_job(
            app, "ep-local", status="done", public_url="http://127.0.0.1:5000/audio/x.mp3"
        )

        resp = app.test_client().get("/tab/feed")
        html = resp.data.decode()

        assert "http://127.0.0.1:5000/feed.xml" in html


class TestSettingsTab:
    def test_get_tab_settings_returns_200(self, client):
        resp = client.get("/tab/settings")
        assert resp.status_code == 200

    def test_settings_contains_form(self, client):
        resp = client.get("/tab/settings")
        html = resp.data.decode()
        assert "<form" in html

    def test_settings_shows_config_fields(self, client):
        resp = client.get("/tab/settings")
        html = resp.data.decode()
        assert "feed-title" in html or "Feed" in html

    def test_save_settings_persists_and_rerenders_form(self, tmp_path):
        import yaml

        config_path = tmp_path / "config.yaml"
        app = create_app(
            db_path=tmp_path / "test.db",
            start_worker=False,
            tts_engine=_mock_engine(),
            pronunciation_path=tmp_path / "pronunciation.json",
            config_path=config_path,
        )
        client = app.test_client()

        resp = client.put(
            "/web/settings",
            data={
                "feed[title]": "New Title",
                "voice[speed]": "1.5",
                "tts[max_parallel]": "2",
            },
        )

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "<form" in html
        assert "Settings saved." in html
        assert 'value="New Title"' in html
        assert 'value="1.5"' in html
        saved = yaml.safe_load(config_path.read_text())
        assert saved["feed"]["title"] == "New Title"
        assert saved["voice"]["speed"] == 1.5
        assert saved["tts"]["max_parallel"] == 2

    def test_save_settings_invalid_section_shows_error_without_persisting(
        self, tmp_path
    ):
        config_path = tmp_path / "config.yaml"
        app = create_app(
            db_path=tmp_path / "test.db",
            start_worker=False,
            tts_engine=_mock_engine(),
            pronunciation_path=tmp_path / "pronunciation.json",
            config_path=config_path,
        )
        client = app.test_client()

        resp = client.put("/web/settings", data={"bogus[field]": "x"})

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "<form" in html
        assert "unknown settings sections" in html.lower()
        assert not config_path.exists()


class TestUnknownTab:
    def test_unknown_tab_returns_404(self, client):
        resp = client.get("/tab/nonexistent")
        assert resp.status_code == 404


class TestCreateJobEndpoint:
    def test_post_valid_url_creates_job(self, app, client):
        resp = client.post(
            "/web/jobs",
            data={"input_type": "url", "input_value": "https://example.com/article"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "quick-add-input" in html

        conn = get_connection(app.config["DB_PATH"])
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["input_value"] == "https://example.com/article"

    def test_post_empty_input_returns_422(self, client):
        resp = client.post("/web/jobs", data={"input_value": ""})
        assert resp.status_code == 422
        html = resp.data.decode()
        assert "required" in html.lower()

    def test_post_raw_text_detects_type(self, app, client):
        resp = client.post(
            "/web/jobs",
            data={"input_type": "url", "input_value": "Hello, world!"},
        )
        assert resp.status_code == 200
        conn = get_connection(app.config["DB_PATH"])
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        conn.close()
        assert rows[0]["input_type"] == "raw_text"


class TestStaticAssets:
    def test_tokens_css_served(self, client):
        resp = client.get("/static/css/tokens.css")
        assert resp.status_code == 200
        assert b"--bg:" in resp.data
        assert b"--accent:" in resp.data

    def test_app_css_served(self, client):
        resp = client.get("/static/css/app.css")
        assert resp.status_code == 200
