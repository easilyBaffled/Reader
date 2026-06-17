"""Flask application factory and process entrypoint."""

import atexit
import os
import shutil
import sys
from pathlib import Path

from flask import Flask, abort, send_from_directory

from audibleweb.api.routes import api_bp
from audibleweb.api.sse import sse_bp
from audibleweb.web.routes import web_bp
from audibleweb.config import DEFAULT_CONFIG_PATH, AppConfig, load_config
from audibleweb.db import get_connection, migrate
from audibleweb.engines.base import TTSEngine
from audibleweb.engines.kokoro import KokoroEngine
from audibleweb.log import setup_logging
from audibleweb.plugins import PluginRegistry
from audibleweb.worker import Worker

DEFAULT_DB_PATH = Path("data/audibleweb.db")
DEFAULT_PLUGINS_DIR = Path("plugins")
DEFAULT_PRONUNCIATION_PATH = Path("pronunciation.json")


def build_tts_engine(config: AppConfig) -> TTSEngine:
    if config.tts.engine == "kokoro":
        return KokoroEngine(
            base_url=config.tts.base_url,
            api_key=config.tts.api_key or "not-needed",
            max_parallel=config.tts.max_parallel,
        )
    raise ValueError(f"Unsupported TTS engine: {config.tts.engine!r}")


def create_app(
    db_path: str | Path | None = None,
    start_worker: bool = True,
    config: AppConfig | None = None,
    tts_engine: TTSEngine | None = None,
    pronunciation_path: str | Path | None = None,
    config_path: str | Path | None = None,
    plugins_dir: str | Path | None = None,
) -> Flask:
    app = Flask(__name__)

    db_path = Path(db_path or os.environ.get("AUDIBLEWEB_DB_PATH", DEFAULT_DB_PATH)).resolve()
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()
    app.config["DB_PATH"] = db_path

    resolved_config_path = Path(config_path or DEFAULT_CONFIG_PATH)
    config = config or load_config(config_path=resolved_config_path)
    app.config["APP_CONFIG"] = config
    app.config["CONFIG_PATH"] = resolved_config_path
    app.config["PRONUNCIATION_PATH"] = Path(
        pronunciation_path or DEFAULT_PRONUNCIATION_PATH
    )
    plugin_registry = PluginRegistry()
    plugin_registry.load(Path(plugins_dir or DEFAULT_PLUGINS_DIR))
    app.extensions["plugin_registry"] = plugin_registry

    app.extensions["tts_engine"] = tts_engine or build_tts_engine(config)
    app.register_blueprint(api_bp)
    app.register_blueprint(sse_bp)
    app.register_blueprint(web_bp)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/audio/<path:filename>")
    def audio(filename: str):
        audio_dir = db_path.parent / "audio"
        if not (audio_dir / filename).is_file():
            abort(404)
        return send_from_directory(audio_dir, filename)

    @app.get("/feed.xml")
    def feed_xml():
        feed_path = db_path.parent / "feed.xml"
        if not feed_path.is_file():
            abort(404)
        return send_from_directory(db_path.parent, "feed.xml")

    if start_worker:
        import json as _json

        pronunciation_file = app.config["PRONUNCIATION_PATH"]
        pronunciation: dict[str, str] = {}
        if pronunciation_file.exists():
            pronunciation = _json.loads(pronunciation_file.read_text())
        worker = Worker(
            db_path,
            config=config,
            engine=app.extensions["tts_engine"],
            pronunciation=pronunciation,
        )
        worker.start()
        app.extensions["worker"] = worker
        atexit.register(worker.stop)

    return app


def check_ffmpeg() -> None:
    """Exit with a clear error if ffmpeg isn't on PATH (required for audio stitching)."""
    if shutil.which("ffmpeg") is None:
        print(
            "ERROR: ffmpeg not found on PATH. AudibleWeb requires ffmpeg for "
            "audio stitching. Install it (e.g. `brew install ffmpeg`) and retry.",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> None:
    check_ffmpeg()
    config = load_config()
    setup_logging(config.logging)
    app = create_app(config=config)
    app.run(debug=True)


if __name__ == "__main__":
    main()
