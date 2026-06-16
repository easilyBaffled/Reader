"""Server-sent events for live job progress (docs/design.md sec 7)."""

from __future__ import annotations

import json
import time
from typing import Generator

from flask import Blueprint, Response, current_app, stream_with_context

from audibleweb.db import get_connection

sse_bp = Blueprint("sse", __name__, url_prefix="/api/jobs")

_POLL_INTERVAL = 1.0
_TERMINAL = frozenset({"done", "failed"})


def _progress(db_path, job_id: str) -> dict | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        chunks_total, chunks_done = 0, 0
        if job["status"] == "generating":
            row2 = conn.execute(
                "SELECT COUNT(*) AS total,"
                " SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done"
                " FROM chunks WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row2:
                chunks_total = row2["total"] or 0
                chunks_done = row2["done"] or 0
        return {
            "id": job["id"],
            "status": job["status"],
            "title": job["title"],
            "error": job["error"],
            "chunks_done": chunks_done,
            "chunks_total": chunks_total,
        }
    finally:
        conn.close()


def _event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _job_stream(db_path, job_id: str) -> Generator[str, None, None]:
    last_state: dict | None = None
    while True:
        state = _progress(db_path, job_id)
        if state is None:
            yield _event({"error": "job not found", "id": job_id})
            return
        if state != last_state:
            yield _event(state)
            last_state = state
        if state["status"] in _TERMINAL:
            return
        time.sleep(_POLL_INTERVAL)


@sse_bp.get("/<job_id>/stream")
def job_stream(job_id: str):
    db_path = current_app.config["DB_PATH"]
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return Response(
        stream_with_context(_job_stream(db_path, job_id)),
        content_type="text/event-stream",
        headers=headers,
    )
