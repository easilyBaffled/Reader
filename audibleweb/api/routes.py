"""REST API for jobs, voices, and pronunciations (docs/design.md sec 7).

`/api/feed` and `/api/settings` are intentionally out of scope here -- they're
covered by reader-ksd (publish workflow) and reader-8f2.7.1 (settings, backed
by config.py) respectively.

Job status machine (docs/design.md sec 5):

    queued -> extracting -> normalizing -> generating -> publishing -> done
      |                (any stage can fail) -> failed                   |
      `-------------------- pause/resume <-> paused --------------------'

`retry` only applies to `failed` jobs. `pause` applies to any job that hasn't
reached a terminal state yet (including `queued`, so a job can be paused
before the worker picks it up). `resume` only applies to `paused` jobs.
Invalid transitions return 409.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from flask import Blueprint, Response, current_app, jsonify, request, send_file
from werkzeug.utils import secure_filename

from audibleweb.pipeline.queue import cleanup_job_audio, cleanup_upload
from audibleweb.config import apply_settings_patch, strip_secret_settings
from audibleweb.db import get_connection
from audibleweb.extractors.base import ExtractionError
from audibleweb.extractors.rss import RSSImportExtractor
from audibleweb.lib.voice import InvalidVoiceSpecError, parse_voice_spec

api_bp = Blueprint("api", __name__, url_prefix="/api")

INPUT_TYPES = {"raw_text", "file", "url", "rss"}

PIPELINE_STATUSES = ("extracting", "normalizing", "generating", "publishing")
PAUSABLE_STATUSES = ("queued", *PIPELINE_STATUSES)
TERMINAL_STATUSES = ("done", "failed")
ALL_STATUSES = (*PAUSABLE_STATUSES, *TERMINAL_STATUSES, "paused")

MIN_SPEED = 0.5
MAX_SPEED = 2.0
STALL_THRESHOLD_SEC = 60


def _db() -> sqlite3.Connection:
    return get_connection(current_app.config["DB_PATH"])


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_stalled(job: dict) -> bool:
    """A job whose worker died (process killed/reloaded) mid-stage: status
    never reaches a terminal value because nothing is left to call fail_job."""
    if job["status"] not in PIPELINE_STATUSES or not job.get("heartbeat_at"):
        return False
    try:
        last_beat = datetime.fromisoformat(job["heartbeat_at"])
    except ValueError:
        return False
    return datetime.now(UTC) - last_beat > timedelta(seconds=STALL_THRESHOLD_SEC)


def _job_to_dict(row: sqlite3.Row) -> dict:
    job = dict(row)
    voice_config = job.get("voice_config")
    job["voice_config"] = json.loads(voice_config) if voice_config else None

    if _is_stalled(job):
        job["stalled_stage"] = job["status"]
        last_beat = datetime.fromisoformat(job["heartbeat_at"])
        job["stalled_for_sec"] = int((datetime.now(UTC) - last_beat).total_seconds())
        job["status"] = "stalled"

    return job


def _fetch_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


# --- /api/jobs ----------------------------------------------------------------


@api_bp.post("/jobs")
def create_job():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    input_value = body.get("input")
    input_type = body.get("type")
    voice_config = body.get("voice_config")

    if not isinstance(input_value, str) or not input_value.strip():
        return jsonify({"error": "'input' is required"}), 400
    if input_type not in INPUT_TYPES:
        return jsonify({"error": f"'type' must be one of {sorted(INPUT_TYPES)}"}), 400
    if voice_config is not None:
        error = _validate_voice_config(voice_config)
        if error:
            return jsonify({"error": error}), 400

    job_id = str(uuid.uuid4())
    now = _now()
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO jobs (id, status, input_type, input_value, voice_config, "
            "created_at, updated_at) VALUES (?, 'queued', ?, ?, ?, ?, ?)",
            (
                job_id,
                input_type,
                input_value,
                json.dumps(voice_config) if voice_config is not None else None,
                now,
                now,
            ),
        )
        conn.commit()
        row = _fetch_job(conn, job_id)
    finally:
        conn.close()

    return jsonify(_job_to_dict(row)), 201


def _validate_voice_config(voice_config: object) -> str | None:
    if not isinstance(voice_config, dict):
        return "'voice_config' must be an object"

    voice = voice_config.get("voice")
    if voice is not None:
        if not isinstance(voice, str):
            return "'voice_config.voice' must be a string"
        try:
            parse_voice_spec(voice)
        except InvalidVoiceSpecError as exc:
            return f"'voice_config.voice' invalid: {exc}"

    speed = voice_config.get("speed")
    if speed is not None:
        valid_speed = isinstance(speed, (int, float)) and not isinstance(speed, bool)
        if not valid_speed or not (MIN_SPEED <= speed <= MAX_SPEED):
            return f"'voice_config.speed' must be between {MIN_SPEED} and {MAX_SPEED}"

    return None


@api_bp.get("/jobs")
def list_jobs():
    status = request.args.get("status")
    if status is not None and status not in ALL_STATUSES:
        return jsonify({"error": f"unknown status {status!r}"}), 400

    conn = _db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
    finally:
        conn.close()

    return jsonify([_job_to_dict(row) for row in rows])


@api_bp.get("/jobs/<job_id>")
def get_job(job_id: str):
    conn = _db()
    try:
        row = _fetch_job(conn, job_id)
    finally:
        conn.close()

    if row is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(_job_to_dict(row))


@api_bp.get("/jobs/<job_id>/audio")
def download_job_audio(job_id: str):
    conn = _db()
    try:
        row = _fetch_job(conn, job_id)
    finally:
        conn.close()

    if row is None:
        return jsonify({"error": "job not found"}), 404
    if not row["audio_path"] or not Path(row["audio_path"]).exists():
        return jsonify({"error": "audio not available"}), 404

    filename = secure_filename(row["title"] or job_id) or job_id
    return send_file(
        row["audio_path"],
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=f"{filename}.mp3",
    )


@api_bp.delete("/jobs/<job_id>")
def delete_job(job_id: str):
    conn = _db()
    try:
        row = _fetch_job(conn, job_id)
        if row is None:
            return jsonify({"error": "job not found"}), 404

        if row["audio_path"]:
            Path(row["audio_path"]).unlink(missing_ok=True)

        data_dir = Path(current_app.config["DB_PATH"]).parent
        cleanup_job_audio(data_dir, job_id)
        cleanup_upload(data_dir, job_id)

        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()

    return "", 204


@api_bp.post("/jobs/<job_id>/retry")
def retry_job(job_id: str):
    conn = _db()
    try:
        row = _fetch_job(conn, job_id)
    finally:
        conn.close()
    if row is None:
        return jsonify({"error": "job not found"}), 404

    # A stalled job's worker died mid-stage, so its DB status is still
    # whatever pipeline stage it was in, not 'failed' -- accept retry from
    # that exact stage too, but only once it's actually stalled (heartbeat
    # stale), so an in-progress job can't be yanked out from under its worker.
    valid_from = {row["status"]} if _is_stalled(dict(row)) else {"failed"}
    return _transition(job_id, valid_from=valid_from, new_status="queued", clear_error=True)


@api_bp.post("/jobs/<job_id>/pause")
def pause_job(job_id: str):
    return _transition(job_id, valid_from=set(PAUSABLE_STATUSES), new_status="paused")


@api_bp.post("/jobs/<job_id>/resume")
def resume_job(job_id: str):
    return _transition(job_id, valid_from={"paused"}, new_status="queued")


def _transition(
    job_id: str, *, valid_from: set[str], new_status: str, clear_error: bool = False
):
    conn = _db()
    try:
        row = _fetch_job(conn, job_id)
        if row is None:
            return jsonify({"error": "job not found"}), 404

        if row["status"] not in valid_from:
            return (
                jsonify(
                    {
                        "error": (
                            f"cannot transition job from status '{row['status']}' "
                            f"to '{new_status}' (must be one of "
                            f"{sorted(valid_from)})"
                        )
                    }
                ),
                409,
            )

        if clear_error:
            conn.execute(
                "UPDATE jobs SET status = ?, error = NULL, updated_at = ? WHERE id = ?",
                (new_status, _now(), job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, _now(), job_id),
            )
        conn.commit()
        row = _fetch_job(conn, job_id)
    finally:
        conn.close()

    return jsonify(_job_to_dict(row))


# --- /api/voices ----------------------------------------------------------------

VOICE_SAMPLE_TEXT = "This is a short preview, so you can hear what this voice sounds like."

# Voice list is static per engine process, so a sample never goes stale --
# caching avoids re-hitting the TTS engine (often max_parallel=1, so repeat
# clicks would otherwise queue behind each other) on every preview click.
_voice_sample_cache: dict[str, bytes] = {}


@api_bp.get("/voices")
def list_voices():
    engine = current_app.extensions["tts_engine"]
    try:
        voices = asyncio.run(engine.list_voices())
    except httpx.HTTPError as exc:
        return jsonify({"error": f"TTS engine unreachable: {exc}"}), 502

    return jsonify({"voices": voices})


@api_bp.get("/voices/<name>/sample")
def voice_sample(name: str):
    cached = _voice_sample_cache.get(name)
    if cached is not None:
        return Response(cached, mimetype="audio/wav")

    engine = current_app.extensions["tts_engine"]
    try:
        audio = asyncio.run(engine.synthesize(VOICE_SAMPLE_TEXT, name))
    except Exception as exc:
        return jsonify({"error": f"TTS engine unreachable: {exc}"}), 502

    _voice_sample_cache[name] = audio
    return Response(audio, mimetype="audio/wav")


# --- /api/pronunciations ----------------------------------------------------------------


@api_bp.get("/pronunciations")
def list_pronunciations():
    return jsonify(_load_pronunciations())


@api_bp.put("/pronunciations")
def upsert_pronunciation():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    word = body.get("word")
    replacement = body.get("replacement")
    if not isinstance(word, str) or not word.strip():
        return jsonify({"error": "'word' is required"}), 400
    if not isinstance(replacement, str) or not replacement.strip():
        return jsonify({"error": "'replacement' is required"}), 400

    pronunciations = _load_pronunciations()
    pronunciations[word] = replacement
    _save_pronunciations(pronunciations)
    return jsonify(pronunciations)


@api_bp.delete("/pronunciations/<word>")
def delete_pronunciation(word: str):
    pronunciations = _load_pronunciations()
    if word not in pronunciations:
        return jsonify({"error": "pronunciation not found"}), 404

    del pronunciations[word]
    _save_pronunciations(pronunciations)
    return "", 204


def _load_pronunciations() -> dict[str, str]:
    path: Path = current_app.config["PRONUNCIATION_PATH"]
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _save_pronunciations(pronunciations: dict[str, str]) -> None:
    path: Path = current_app.config["PRONUNCIATION_PATH"]
    path.write_text(json.dumps(pronunciations, indent=2, sort_keys=True) + "\n")


# --- /api/settings ----------------------------------------------------------------


@api_bp.get("/settings")
def get_settings():
    config = current_app.config["APP_CONFIG"]
    return jsonify(strip_secret_settings(dataclasses.asdict(config)))


@api_bp.put("/settings")
def update_settings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    try:
        new_config = apply_settings_patch(current_app.config["CONFIG_PATH"], body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    current_app.config["APP_CONFIG"] = new_config
    return jsonify(strip_secret_settings(dataclasses.asdict(new_config)))


# --- /api/feeds ----------------------------------------------------------------


@api_bp.get("/feeds")
def list_feeds():
    config = current_app.config["APP_CONFIG"]
    return jsonify({"feeds": config.extraction.rss_feeds})


@api_bp.post("/feeds")
def add_feed():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    url = body.get("url")
    if not isinstance(url, str) or not url.strip():
        return jsonify({"error": "'url' is required"}), 400
    url = url.strip()

    config = current_app.config["APP_CONFIG"]
    if url in config.extraction.rss_feeds:
        return jsonify({"error": "feed already subscribed"}), 400

    conn = _db()
    try:
        try:
            seen_count = asyncio.run(RSSImportExtractor().first_subscribe(url, conn))
        except ExtractionError as exc:
            return jsonify({"error": str(exc)}), 502
    finally:
        conn.close()

    new_feeds = [*config.extraction.rss_feeds, url]
    new_config = apply_settings_patch(
        current_app.config["CONFIG_PATH"], {"extraction": {"rss_feeds": new_feeds}}
    )
    current_app.config["APP_CONFIG"] = new_config
    return (
        jsonify({"feeds": new_config.extraction.rss_feeds, "marked_seen": seen_count}),
        201,
    )


@api_bp.delete("/feeds")
def remove_feed():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    url = body.get("url")
    config = current_app.config["APP_CONFIG"]
    if not isinstance(url, str) or url not in config.extraction.rss_feeds:
        return jsonify({"error": "feed not found"}), 404

    new_feeds = [f for f in config.extraction.rss_feeds if f != url]
    new_config = apply_settings_patch(
        current_app.config["CONFIG_PATH"], {"extraction": {"rss_feeds": new_feeds}}
    )
    current_app.config["APP_CONFIG"] = new_config
    return "", 204
