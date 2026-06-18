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
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from audibleweb.config import AppConfig
from audibleweb.core.pipeline import PipelinePausedError, run_pipeline
from audibleweb.db import get_connection
from audibleweb.engines.base import TTSEngine
from audibleweb.extractors.base import ExtractionError
from audibleweb.extractors.rss import RSSImportExtractor
from audibleweb.log import set_job_id
from audibleweb.pipeline.queue import fail_job

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SEC = 1.0
HEARTBEAT_INTERVAL_SEC = 30.0


class Worker:
    def __init__(
        self,
        db_path: str | Path,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
        *,
        config: AppConfig | None = None,
        engine: TTSEngine | None = None,
        pronunciation: dict[str, str] | None = None,
        rss_extractor: RSSImportExtractor | None = None,
    ):
        self.db_path = db_path
        self.poll_interval = poll_interval
        self._config = config
        self._engine = engine
        self._pronunciation = pronunciation or {}
        self._rss_extractor = rss_extractor
        self._last_rss_poll = 0.0
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

    async def _maybe_poll_rss(
        self, conn: sqlite3.Connection, config: AppConfig
    ) -> None:
        if time.monotonic() - self._last_rss_poll < config.extraction.rss_poll_interval:
            return
        await _poll_rss_feeds(conn, config, extractor=self._rss_extractor)
        self._last_rss_poll = time.monotonic()

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._ready.set()

        data_dir = Path(self.db_path).parent
        conn = get_connection(self.db_path)

        config = self._config
        engine = self._engine
        if config is None or engine is None:
            from audibleweb.config import load_config
            from audibleweb.engines.kokoro import KokoroEngine

            config = config or load_config()
            if engine is None:
                engine = KokoroEngine(
                    base_url=config.tts.base_url,
                    api_key=config.tts.api_key or "not-needed",
                    max_parallel=config.tts.max_parallel,
                )

        try:
            while not self._stop_event.is_set():
                await self._maybe_poll_rss(conn, config)
                job_id = _claim_next_job(conn)
                if job_id is not None:
                    await _run_with_heartbeat(
                        conn,
                        job_id,
                        data_dir,
                        config=config,
                        engine=engine,
                        pronunciation=self._pronunciation,
                    )
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
    data_dir: Path,
    *,
    config: AppConfig,
    engine: TTSEngine,
    pronunciation: dict[str, str],
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
            await run_pipeline(
                conn,
                job_id,
                config=config,
                engine=engine,
                pronunciation=pronunciation,
                data_dir=data_dir,
            )
            logger.info("job done")
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
    except PipelinePausedError:
        logger.info("job paused")
    except Exception as exc:
        logger.exception("job failed")
        fail_job(conn, job_id, str(exc), data_dir)
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


async def _poll_rss_feeds(
    conn: sqlite3.Connection,
    config: AppConfig,
    *,
    extractor: RSSImportExtractor | None = None,
) -> None:
    extractor = extractor or RSSImportExtractor()
    for feed_url in config.extraction.rss_feeds:
        try:
            articles = await extractor.list_new_articles(feed_url, conn)
        except ExtractionError as exc:
            logger.warning("rss poll failed for %s: %s", feed_url, exc)
            continue
        for article in articles:
            if not article.source_url:
                logger.warning("rss entry from %s has no link, skipping", feed_url)
                continue
            _insert_rss_job(conn, article.source_url)


def _insert_rss_job(conn: sqlite3.Connection, url: str) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO jobs (id, status, input_type, input_value, created_at, updated_at) "
        "VALUES (?, 'queued', 'url', ?, ?, ?)",
        (str(uuid.uuid4()), url, now, now),
    )
    conn.commit()
