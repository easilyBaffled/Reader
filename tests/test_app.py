import pytest

from audibleweb.app import check_ffmpeg, create_app


def test_audio_route_serves_published_file(tmp_path):
    db_path = tmp_path / "test.db"
    app = create_app(db_path=db_path, start_worker=False)
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "my-episode.mp3").write_bytes(b"fake-mp3-bytes")

    resp = app.test_client().get("/audio/my-episode.mp3")

    assert resp.status_code == 200
    assert resp.data == b"fake-mp3-bytes"


def test_audio_route_serves_file_with_relative_db_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app(db_path="data/audibleweb.db", start_worker=False)
    audio_dir = tmp_path / "data" / "audio"
    audio_dir.mkdir(parents=True)
    (audio_dir / "my-episode.mp3").write_bytes(b"fake-mp3-bytes")

    resp = app.test_client().get("/audio/my-episode.mp3")

    assert resp.status_code == 200
    assert resp.data == b"fake-mp3-bytes"


def test_audio_route_404_for_missing_file(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", start_worker=False)

    resp = app.test_client().get("/audio/nope.mp3")

    assert resp.status_code == 404


def test_feed_xml_route_serves_published_feed(tmp_path):
    db_path = tmp_path / "test.db"
    app = create_app(db_path=db_path, start_worker=False)
    (tmp_path / "feed.xml").write_text("<rss>fake feed</rss>", encoding="utf-8")

    resp = app.test_client().get("/feed.xml")

    assert resp.status_code == 200
    assert b"fake feed" in resp.data


def test_feed_xml_route_404_before_first_publish(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", start_worker=False)

    resp = app.test_client().get("/feed.xml")

    assert resp.status_code == 404


def test_healthz(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", start_worker=False)
    client = app.test_client()

    resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_create_app_runs_migrations(tmp_path):
    db_path = tmp_path / "test.db"
    create_app(db_path=db_path, start_worker=False)

    assert db_path.exists()


def test_create_app_starts_worker(tmp_path):
    app = create_app(db_path=tmp_path / "test.db")

    try:
        worker = app.extensions["worker"]
        assert worker._thread.is_alive()
    finally:
        app.extensions["worker"].stop()


def test_check_ffmpeg_missing_exits(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(SystemExit):
        check_ffmpeg()


def test_check_ffmpeg_present_ok(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

    check_ffmpeg()  # must not raise
