"""Flask application factory and process entrypoint."""

import os
import shutil
import sys
from pathlib import Path

from flask import Flask

from audibleweb.db import get_connection, migrate

DEFAULT_DB_PATH = Path("data/audibleweb.db")


def create_app(db_path: str | Path | None = None) -> Flask:
    app = Flask(__name__)

    db_path = Path(db_path or os.environ.get("AUDIBLEWEB_DB_PATH", DEFAULT_DB_PATH))
    conn = get_connection(db_path)
    migrate(conn)
    conn.close()
    app.config["DB_PATH"] = db_path

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

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
    app = create_app()
    app.run(debug=True)


if __name__ == "__main__":
    main()
