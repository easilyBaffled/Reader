"""Web UI blueprint — Jinja HTML views for the HTMX interface."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from flask import Blueprint, current_app, render_template, request

from audibleweb.api.routes import _job_to_dict
from audibleweb.db import get_connection

web_bp = Blueprint(
    "web",
    __name__,
    template_folder="templates",
)

_TABS = frozenset({"inbox", "queue", "feed", "settings"})

_INPUT_TYPES = frozenset({"url", "raw_text", "file", "rss"})


def _db() -> sqlite3.Connection:
    return get_connection(current_app.config["DB_PATH"])


def _load_jobs() -> list[dict]:
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [_job_to_dict(row) for row in rows]
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
        if config and config.publisher and getattr(config.publisher, "repo", None):
            ctx["feed_url"] = (
                f"https://{config.publisher.repo.split('/')[0]}.github.io"
                f"/{config.publisher.repo.split('/')[-1]}/feed.xml"
                if "/" in config.publisher.repo
                else None
            )
    elif tab_name == "settings":
        ctx["config"] = current_app.config.get("APP_CONFIG")

    return render_template(f"partials/{tab_name}.html", **ctx)


@web_bp.post("/web/jobs")
def create_job():
    input_value = (request.form.get("input_value") or "").strip()
    input_type = request.form.get("input_type") or "url"

    if not input_value:
        return (
            render_template(
                "partials/inbox.html",
                active_tab="inbox",
                error="A URL or text is required.",
            ),
            422,
        )

    if input_type not in _INPUT_TYPES:
        input_type = "url"

    if input_type == "url" and not input_value.startswith(("http://", "https://")):
        input_type = "raw_text"

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO jobs (id, status, input_type, input_value, created_at, updated_at)"
            " VALUES (?, 'queued', ?, ?, ?, ?)",
            (job_id, input_type, input_value, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    return render_template(
        "partials/queue.html",
        active_tab="queue",
        jobs=_load_jobs(),
    )
