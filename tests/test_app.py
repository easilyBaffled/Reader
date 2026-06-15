import pytest

from audibleweb.app import check_ffmpeg, create_app


def test_healthz(tmp_path):
    app = create_app(db_path=tmp_path / "test.db")
    client = app.test_client()

    resp = client.get("/healthz")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_create_app_runs_migrations(tmp_path):
    db_path = tmp_path / "test.db"
    create_app(db_path=db_path)

    assert db_path.exists()


def test_check_ffmpeg_missing_exits(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(SystemExit):
        check_ffmpeg()


def test_check_ffmpeg_present_ok(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

    check_ffmpeg()  # must not raise
