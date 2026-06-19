"""Web UI blueprint — Jinja HTML views for the HTMX interface."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, current_app, render_template, request
from werkzeug.utils import secure_filename

from audibleweb.api.routes import (
    _job_to_dict,
    _load_pronunciations,
    _save_pronunciations,
    _validate_voice_config,
)
from audibleweb.config import SETTINGS_SECTION_CLASSES, apply_settings_patch
from audibleweb.db import get_connection
from audibleweb.extractors.base import ExtractionError
from audibleweb.extractors.rss import RSSImportExtractor
from audibleweb.lib.voice import InvalidVoiceSpecError, VoiceWeight, parse_voice_spec
from audibleweb.pipeline.queue import upload_dir

web_bp = Blueprint(
    "web",
    __name__,
    template_folder="templates",
)

_TABS = frozenset({"inbox", "queue", "feed", "settings"})

_INPUT_TYPES = frozenset({"url", "raw_text", "file", "rss"})

_SETTINGS_FORM_KEY_RE = re.compile(r"^(\w+)\[(\w+)\]$")


def _db() -> sqlite3.Connection:
    return get_connection(current_app.config["DB_PATH"])


def _load_jobs() -> list[dict]:
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        jobs = [_job_to_dict(row) for row in rows]
        for job in jobs:
            if job["status"] == "generating" or job.get("stalled_stage") == "generating":
                progress = conn.execute(
                    "SELECT COUNT(*) AS total,"
                    " SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done"
                    " FROM chunks WHERE job_id = ?",
                    (job["id"],),
                ).fetchone()
                job["chunks_total"] = progress["total"] or 0
                job["chunks_done"] = progress["done"] or 0
        return jobs
    finally:
        conn.close()


def _load_done_jobs() -> list[dict]:
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'done' ORDER BY created_at DESC"
        ).fetchall()
        return [_job_to_dict(row) for row in rows]
    finally:
        conn.close()


@web_bp.get("/")
def index():
    return render_template("index.html", active_tab="queue", jobs=_load_jobs())


def _voice_ui_state(default: str) -> tuple[str, list[VoiceWeight]]:
    """Parse a voice spec string into (mode, voices) for pre-populating the builder.

    Falls back to single-voice mode with the raw string as the voice name if
    `default` isn't valid spec syntax, so a malformed config.yaml value never
    crashes the Settings page."""
    try:
        spec = parse_voice_spec(default)
    except InvalidVoiceSpecError:
        return "single", [VoiceWeight(name=default, weight=1.0)]
    if spec.type == "weighted":
        return "weighted", spec.voices
    if len(spec.voices) == 1:
        return "single", spec.voices
    return "native", spec.voices


@web_bp.get("/tab/<tab_name>")
def tab(tab_name: str):
    if tab_name not in _TABS:
        return "", 404

    ctx: dict = {"active_tab": tab_name}
    if tab_name == "queue":
        ctx["jobs"] = _load_jobs()
    elif tab_name == "feed":
        ctx["episodes"] = _load_done_jobs()
        config = current_app.config.get("APP_CONFIG")
        if config and config.publisher and config.publisher.type == "local":
            ctx["feed_url"] = f"http://{config.server.host}:{config.server.port}/feed.xml"
        elif config and config.publisher and getattr(config.publisher, "repo", None):
            ctx["feed_url"] = (
                f"https://{config.publisher.repo.split('/')[0]}.github.io"
                f"/{config.publisher.repo.split('/')[-1]}/feed.xml"
                if "/" in config.publisher.repo
                else None
            )
    elif tab_name == "settings":
        config = current_app.config.get("APP_CONFIG")
        ctx["config"] = config
        default_voice = config.voice.default if config else "af_heart"
        ctx["voice_mode"], ctx["voice_voices"] = _voice_ui_state(default_voice)

    return render_template(f"partials/{tab_name}.html", **ctx)


@web_bp.post("/web/jobs")
def create_job():
    job_id = str(uuid.uuid4())
    input_value = (request.form.get("input_value") or "").strip()
    input_type = request.form.get("input_type") or "url"

    if input_type not in _INPUT_TYPES:
        input_type = "url"

    if input_type == "file":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return (
                render_template(
                    "partials/inbox.html",
                    active_tab="inbox",
                    error="A file is required.",
                ),
                422,
            )
        data_dir = Path(current_app.config["DB_PATH"]).parent
        dest_dir = upload_dir(data_dir, job_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = secure_filename(upload.filename) or (
            f"upload{Path(upload.filename).suffix.lower()}"
        )
        dest_path = dest_dir / filename
        upload.save(dest_path)
        input_value = str(dest_path)
    elif not input_value:
        return (
            render_template(
                "partials/inbox.html",
                active_tab="inbox",
                error="A URL or text is required.",
            ),
            422,
        )

    if input_type == "url" and not input_value.startswith(("http://", "https://")):
        input_type = "raw_text"

    voice_value = (request.form.get("voice_config[voice]") or "").strip()
    voice_config = {"voice": voice_value} if voice_value else None
    if voice_config is not None:
        error = _validate_voice_config(voice_config)
        if error:
            return (
                render_template(
                    "partials/inbox.html",
                    active_tab="inbox",
                    error=error,
                ),
                422,
            )

    now = datetime.now(UTC).isoformat()
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO jobs (id, status, input_type, input_value, voice_config,"
            " created_at, updated_at) VALUES (?, 'queued', ?, ?, ?, ?, ?)",
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
    finally:
        conn.close()

    return render_template(
        "partials/queue.html",
        active_tab="queue",
        jobs=_load_jobs(),
    )


def _coerce_settings_field(value: str, field_type: object) -> object:
    type_name = field_type if isinstance(field_type, str) else getattr(
        field_type, "__name__", ""
    )
    if type_name == "int":
        return int(value)
    if type_name == "float":
        return float(value)
    if type_name == "bool":
        return value.lower() in ("true", "1", "yes", "on")
    return value


def _parse_settings_form(form) -> dict[str, dict]:
    """Convert HTML form fields like 'feed[title]' into a nested settings patch,
    coercing each value to its dataclass field's declared type."""
    field_types = {
        section: {f.name: f.type for f in dataclasses.fields(cls)}
        for section, cls in SETTINGS_SECTION_CLASSES.items()
    }
    patch: dict[str, dict] = {}
    for key, value in form.items():
        match = _SETTINGS_FORM_KEY_RE.match(key)
        if not match:
            continue
        section, field_name = match.groups()
        field_type = field_types.get(section, {}).get(field_name, str)
        patch.setdefault(section, {})[field_name] = _coerce_settings_field(
            value, field_type
        )
    return patch


@web_bp.put("/web/settings")
def save_settings():
    patch = _parse_settings_form(request.form)
    try:
        new_config = apply_settings_patch(current_app.config["CONFIG_PATH"], patch)
    except ValueError as exc:
        config = current_app.config.get("APP_CONFIG")
        voice_mode, voice_voices = _voice_ui_state(
            config.voice.default if config else "af_heart"
        )
        return render_template(
            "partials/settings.html",
            active_tab="settings",
            config=config,
            save_error=str(exc),
            voice_mode=voice_mode,
            voice_voices=voice_voices,
        )

    current_app.config["APP_CONFIG"] = new_config
    voice_mode, voice_voices = _voice_ui_state(new_config.voice.default)
    return render_template(
        "partials/settings.html",
        active_tab="settings",
        config=new_config,
        saved=True,
        voice_mode=voice_mode,
        voice_voices=voice_voices,
    )


@web_bp.get("/web/pronunciations")
def list_pronunciations():
    return render_template(
        "partials/pronunciation_list.html",
        pronunciations=_load_pronunciations(),
    )


@web_bp.put("/web/pronunciations")
def upsert_pronunciation():
    word = (request.form.get("word") or "").strip()
    replacement = (request.form.get("replacement") or "").strip()
    if word and replacement:
        pronunciations = _load_pronunciations()
        pronunciations[word] = replacement
        _save_pronunciations(pronunciations)
    return render_template(
        "partials/pronunciation_list.html",
        pronunciations=_load_pronunciations(),
    )


@web_bp.delete("/web/pronunciations/<word>")
def delete_pronunciation(word: str):
    pronunciations = _load_pronunciations()
    pronunciations.pop(word, None)
    _save_pronunciations(pronunciations)
    return render_template(
        "partials/pronunciation_list.html",
        pronunciations=_load_pronunciations(),
    )


@web_bp.get("/web/feeds")
def list_feeds():
    config = current_app.config["APP_CONFIG"]
    return render_template(
        "partials/rss_feed_list.html", feeds=config.extraction.rss_feeds
    )


@web_bp.post("/web/feeds")
def add_feed():
    url = (request.form.get("url") or "").strip()
    config = current_app.config["APP_CONFIG"]
    error = None

    if not url:
        error = "A feed URL is required."
    elif url in config.extraction.rss_feeds:
        error = "That feed is already subscribed."
    else:
        conn = _db()
        try:
            try:
                asyncio.run(RSSImportExtractor().first_subscribe(url, conn))
            except ExtractionError as exc:
                error = str(exc)
        finally:
            conn.close()

    if error:
        return render_template(
            "partials/rss_feed_list.html",
            feeds=config.extraction.rss_feeds,
            error=error,
        )

    new_feeds = [*config.extraction.rss_feeds, url]
    new_config = apply_settings_patch(
        current_app.config["CONFIG_PATH"], {"extraction": {"rss_feeds": new_feeds}}
    )
    current_app.config["APP_CONFIG"] = new_config
    return render_template(
        "partials/rss_feed_list.html", feeds=new_config.extraction.rss_feeds
    )


@web_bp.delete("/web/feeds")
def remove_feed():
    # Tolerates an absent url (no-op filter) rather than 404ing like
    # /api/feeds DELETE -- HTMX always re-renders the fragment either way,
    # and the delete buttons here only ever submit urls already in the list.
    url = request.args.get("url", "")
    config = current_app.config["APP_CONFIG"]
    new_feeds = [f for f in config.extraction.rss_feeds if f != url]

    new_config = apply_settings_patch(
        current_app.config["CONFIG_PATH"], {"extraction": {"rss_feeds": new_feeds}}
    )
    current_app.config["APP_CONFIG"] = new_config
    return render_template(
        "partials/rss_feed_list.html", feeds=new_config.extraction.rss_feeds
    )
