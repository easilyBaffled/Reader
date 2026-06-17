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

    def test_pronunciations_div_has_own_hx_target(self, client):
        """Regression guard: hx-target is inheritable in HTMX, and the
        surrounding <form> declares hx-target="this". Without its own
        explicit hx-target, #pronunciations-list's "this" would resolve to
        the ancestor form instead of itself, corrupting the whole form on
        every Settings page load. This only checks the static markup (the
        runtime HTMX inheritance behavior needs a real browser), but it
        still catches someone removing the attribute as "redundant"."""
        resp = client.get("/tab/settings")
        html = resp.data.decode()
        pronunciations_div = html.split('id="pronunciations-list"')[1].split(">")[0]
        assert 'hx-target="this"' in pronunciations_div

    def test_settings_single_voice_default_shows_single_mode(self, tmp_path):
        from audibleweb.app import create_app
        from audibleweb.config import AppConfig, VoiceConfig

        config = AppConfig(voice=VoiceConfig(default="af_heart"))
        app = create_app(
            db_path=tmp_path / "test.db",
            start_worker=False,
            tts_engine=_mock_engine(),
            pronunciation_path=tmp_path / "pronunciation.json",
            config=config,
        )

        resp = app.test_client().get("/tab/settings")
        html = resp.data.decode()

        assert 'value="single" checked' in html
        assert 'data-initial="af_heart"' in html

    def test_settings_native_blend_default_shows_native_mode_and_slots(self, tmp_path):
        from audibleweb.app import create_app
        from audibleweb.config import AppConfig, VoiceConfig

        config = AppConfig(voice=VoiceConfig(default="af_heart+af_bella"))
        app = create_app(
            db_path=tmp_path / "test.db",
            start_worker=False,
            tts_engine=_mock_engine(),
            pronunciation_path=tmp_path / "pronunciation.json",
            config=config,
        )

        resp = app.test_client().get("/tab/settings")
        html = resp.data.decode()

        assert 'value="native" checked' in html
        assert html.count('class="voice-blend-slot"') == 3
        assert 'data-initial="af_heart"' in html
        assert 'data-initial="af_bella"' in html

    def test_settings_weighted_blend_default_shows_weighted_mode_and_weights(
        self, tmp_path
    ):
        from audibleweb.app import create_app
        from audibleweb.config import AppConfig, VoiceConfig

        config = AppConfig(voice=VoiceConfig(default="af_heart:0.7+af_bella:0.3"))
        app = create_app(
            db_path=tmp_path / "test.db",
            start_worker=False,
            tts_engine=_mock_engine(),
            pronunciation_path=tmp_path / "pronunciation.json",
            config=config,
        )

        resp = app.test_client().get("/tab/settings")
        html = resp.data.decode()

        assert 'value="weighted" checked' in html
        assert 'value="0.7"' in html
        assert 'value="0.3"' in html

    def test_settings_voice_hidden_field_keeps_field_name(self, client):
        resp = client.get("/tab/settings")
        html = resp.data.decode()
        assert 'name="voice[default]"' in html

    def test_settings_save_native_blend_rerenders_native_mode(self, tmp_path):
        from audibleweb.app import create_app

        config_path = tmp_path / "config.yaml"
        app = create_app(
            db_path=tmp_path / "test.db",
            start_worker=False,
            tts_engine=_mock_engine(),
            pronunciation_path=tmp_path / "pronunciation.json",
            config_path=config_path,
        )
        client = app.test_client()

        resp = client.put("/web/settings", data={"voice[default]": "af_heart+af_bella"})

        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'value="native" checked' in html
        assert 'data-initial="af_bella"' in html

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


class TestPronunciationsWebUI:
    def test_get_pronunciations_empty_state(self, client):
        resp = client.get("/web/pronunciations")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "no pronunciation" in html.lower()

    def test_get_pronunciations_lists_existing(self, app, client):
        path = app.config["PRONUNCIATION_PATH"]
        path.write_text('{"Kubernetes": "Koo-ber-net-eez"}')

        resp = client.get("/web/pronunciations")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Kubernetes" in html
        assert "Koo-ber-net-eez" in html

    def test_put_pronunciation_adds_and_persists(self, app, client):
        resp = client.put(
            "/web/pronunciations",
            data={"word": "Kubernetes", "replacement": "Koo-ber-net-eez"},
        )

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Kubernetes" in html
        assert "Koo-ber-net-eez" in html
        import json

        saved = json.loads(app.config["PRONUNCIATION_PATH"].read_text())
        assert saved == {"Kubernetes": "Koo-ber-net-eez"}

    def test_delete_pronunciation_removes_and_rerenders(self, app, client):
        path = app.config["PRONUNCIATION_PATH"]
        path.write_text('{"Kubernetes": "Koo-ber-net-eez"}')

        resp = client.delete("/web/pronunciations/Kubernetes")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Kubernetes" not in html
        import json

        saved = json.loads(path.read_text())
        assert saved == {}


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

    def test_post_with_voice_override_stores_voice_config(self, app, client):
        import json

        resp = client.post(
            "/web/jobs",
            data={
                "input_type": "raw_text",
                "input_value": "Hello world this is long enough to be a real article body",
                "voice_config[voice]": "af_heart",
            },
        )
        assert resp.status_code == 200
        conn = get_connection(app.config["DB_PATH"])
        row = conn.execute("SELECT voice_config FROM jobs").fetchone()
        conn.close()
        assert json.loads(row["voice_config"]) == {"voice": "af_heart"}

    def test_post_without_voice_override_stores_none(self, app, client):
        resp = client.post(
            "/web/jobs",
            data={
                "input_type": "raw_text",
                "input_value": "Hello world this is long enough to be a real article body",
            },
        )
        assert resp.status_code == 200
        conn = get_connection(app.config["DB_PATH"])
        row = conn.execute("SELECT voice_config FROM jobs").fetchone()
        conn.close()
        assert row["voice_config"] is None

    def test_post_with_invalid_voice_override_returns_422(self, app, client):
        resp = client.post(
            "/web/jobs",
            data={
                "input_type": "raw_text",
                "input_value": "Hello world this is long enough to be a real article body",
                "voice_config[voice]": "???",
            },
        )
        assert resp.status_code == 422
        conn = get_connection(app.config["DB_PATH"])
        rows = conn.execute("SELECT * FROM jobs").fetchall()
        conn.close()
        assert rows == []


class TestStaticAssets:
    def test_tokens_css_served(self, client):
        resp = client.get("/static/css/tokens.css")
        assert resp.status_code == 200
        assert b"--bg:" in resp.data
        assert b"--accent:" in resp.data

    def test_app_css_served(self, client):
        resp = client.get("/static/css/app.css")
        assert resp.status_code == 200
