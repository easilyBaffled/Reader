"""Background worker: drives queued jobs through the pipeline.

Flask stays fully synchronous (eng-T3/D13) - routes only read and write the
jobs/chunks tables. This worker runs in its own thread with its own asyncio
event loop, polling for queued jobs one at a time (parallelism happens
within a job, at the chunk level).
"""

import asyncio
import contextlib
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from audibleweb.core.pipeline import run_pipeline
from audibleweb.db import get_connection
from audibleweb.log import set_job_id

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SEC = 1.0
HEARTBEAT_INTERVAL_SEC = 30.0


class Worker:
    def __init__(
        self, db_path: str | Path, poll_interval: float = DEFAULT_POLL_INTERVAL_SEC
    ):
        self.db_path = db_path
        self.poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="audibleweb-worker", daemon=True
        )
        self._thread.start()
        self._ready.wait()

    def stop(self) -> None:
        if (
            self._loop is not None
            and self._stop_event is not None
            and not self._loop.is_closed()
        ):
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join()

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._ready.set()

        conn = get_connection(self.db_path)
        try:
            while not self._stop_event.is_set():
                job_id = _claim_next_job(conn)
                if job_id is not None:
                    await _run_with_heartbeat(conn, job_id)
                    continue
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.poll_interval
                    )
                except TimeoutError:
                    pass
        finally:
            conn.close()


async def _run_with_heartbeat(
    conn: sqlite3.Connection,
    job_id: str,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SEC,
) -> None:
    set_job_id(job_id)
    try:
        logger.info("job started")
        _set_heartbeat(conn, job_id)
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(conn, job_id, heartbeat_interval)
        )
        try:
            await run_pipeline(conn, job_id)
            logger.info("job done")
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
    except Exception:
        logger.exception("job failed")
        raise
    finally:
        set_job_id(None)


async def _heartbeat_loop(
    conn: sqlite3.Connection, job_id: str, interval: float
) -> None:
    while True:
        await asyncio.sleep(interval)
        _set_heartbeat(conn, job_id)


def _set_heartbeat(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET heartbeat_at = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), job_id),
    )
    conn.commit()


def _claim_next_job(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
    ).fetchone()
    return row["id"] if row else None
