"""Pipeline orchestration: extract -> normalize -> generate -> publish.

Full implementation (reader-8f2.10), replacing the reader-z4v stub.
Each stage updates jobs.status so SSE progress stream can report it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from audibleweb.config import AppConfig
from audibleweb.core.feed import FeedConfig as CoreFeedConfig
from audibleweb.engines.base import TTSEngine
from audibleweb.extractors.file import FileExtractor
from audibleweb.extractors.raw_text import RawTextExtractor
from audibleweb.extractors.rss import RSSImportExtractor
from audibleweb.extractors.web import WebExtractor
from audibleweb.lib.chunking import chunk_text
from audibleweb.lib.cleaning import apply_pronunciation_overrides, clean_text
from audibleweb.pipeline.normalize import normalize_text
from audibleweb.pipeline.queue import cleanup_job_audio, job_audio_dir
from audibleweb.pipeline.stitch import stitch_chunks
from audibleweb.publishers.base import Episode
from audibleweb.publishers.github_pages import GitHubPagesPublisher
from audibleweb.publishers.local import LocalPublisher

logger = logging.getLogger(__name__)

PROGRESS_EVERY_CHUNKS = 10
PROGRESS_EVERY_SEC = 15.0


class PipelinePausedError(Exception):
    """Raised cooperatively when the job transitions to 'paused' mid-run."""


async def run_pipeline(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    config: AppConfig,
    engine: TTSEngine,
    pronunciation: dict[str, str],
    data_dir: Path,
) -> None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    voice_cfg = json.loads(row["voice_config"]) if row["voice_config"] else {}
    voice = voice_cfg.get("voice") or config.voice.default
    speed = float(voice_cfg.get("speed") or config.voice.speed)

    # --- Stage 1: extracting ---
    _set_status(conn, job_id, "extracting")
    _log_event(
        conn, job_id, "extracting",
        _extracting_detail(row["input_type"], row["input_value"]),
    )
    extractor = _build_extractor(row["input_type"], config)
    article = await extractor.extract(row["input_value"])
    _update_fields(
        conn,
        job_id,
        title=article.title,
        source_url=article.source_url,
        word_count=article.word_count,
    )
    logger.info("extracted %r (%d words)", article.title, article.word_count)

    _check_paused(conn, job_id)

    # --- Stage 2: normalizing (clean + LLM + pronunciation) ---
    _set_status(conn, job_id, "normalizing")
    _log_event(conn, job_id, "normalizing", "Cleaning text")
    text = clean_text(article.text)
    norm_cfg = config.normalization
    if norm_cfg.llm_enabled and norm_cfg.llm_base_url.strip() and norm_cfg.llm_model.strip():
        _log_event(conn, job_id, "normalizing", f"Normalizing via LLM ({norm_cfg.llm_model})")
    text = await normalize_text(text, norm_cfg)
    _log_event(conn, job_id, "normalizing", "Applying pronunciation overrides")
    text = apply_pronunciation_overrides(text, pronunciation)

    _log_event(conn, job_id, "normalizing", "Splitting into chunks")
    text_chunks = chunk_text(text, level="sentence")
    _insert_chunk_rows(conn, job_id, text_chunks)
    logger.info("chunked into %d segments", len(text_chunks))

    _check_paused(conn, job_id)

    # --- Stage 3: generating (parallel TTS) ---
    _set_status(conn, job_id, "generating")
    _log_event(conn, job_id, "generating", f"Synthesizing {len(text_chunks)} segments")
    chunk_paths = await _synthesize_all(
        conn, job_id, text_chunks, engine, voice, speed, data_dir
    )

    _check_paused(conn, job_id)

    # --- Stage 4: publishing (stitch + publish + feed) ---
    _set_status(conn, job_id, "publishing")
    _log_event(conn, job_id, "publishing", "Stitching audio")
    audio_dir = data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_mp3 = audio_dir / f"{job_id}.mp3"
    duration = await stitch_chunks(chunk_paths, output_mp3)
    file_size = output_mp3.stat().st_size

    # Persist the stitched mp3's location now, before attempting to publish --
    # a downstream publish failure (e.g. bad git remote) must not strand an
    # already-generated episode with no way to retrieve it.
    conn.execute(
        "UPDATE jobs SET audio_path=?, audio_duration_sec=?, file_size_bytes=?, "
        "updated_at=? WHERE id=?",
        (str(output_mp3), duration, file_size, datetime.now(UTC).isoformat(), job_id),
    )
    conn.commit()

    now = datetime.now(UTC)
    title = (
        conn.execute("SELECT title FROM jobs WHERE id = ?", (job_id,)).fetchone()[
            "title"
        ]
        or "Untitled"
    )
    episode = Episode(
        title=title,
        published=now,
        duration_sec=duration,
        source_url=article.source_url,
        file_size_bytes=file_size,
    )

    publisher = _build_publisher(
        config, data_dir,
        on_progress=lambda detail: _log_event(conn, job_id, "publishing", detail),
    )
    all_episodes = [episode] + _load_done_episodes(conn)

    public_url, _ = await publisher.publish_and_update_feed(
        episode, output_mp3, all_episodes
    )

    cleanup_job_audio(data_dir, job_id)

    conn.execute(
        "UPDATE jobs SET status='done', public_url=?, updated_at=? WHERE id=?",
        (public_url, datetime.now(UTC).isoformat(), job_id),
    )
    conn.commit()
    logger.info("done: %r -> %s", title, public_url)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_paused(conn: sqlite3.Connection, job_id: str) -> None:
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row and row["status"] == "paused":
        raise PipelinePausedError(f"job {job_id} was paused")


def _set_status(conn: sqlite3.Connection, job_id: str, status: str) -> None:
    conn.execute(
        "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
        (status, datetime.now(UTC).isoformat(), job_id),
    )
    conn.commit()


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _log_event(conn: sqlite3.Connection, job_id: str, stage: str, detail: str) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE jobs SET stage_detail=?, updated_at=? WHERE id=?",
        (detail, now, job_id),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, stage, detail, created_at) VALUES (?, ?, ?, ?)",
        (job_id, stage, detail, now),
    )
    conn.commit()


def _extracting_detail(input_type: str, input_value: str) -> str:
    if input_type == "url":
        return f"Fetching {input_value}"
    if input_type == "file":
        return f"Reading {Path(input_value).name}"
    if input_type == "rss":
        return f"Fetching {input_value}"
    return "Reading pasted text"


def _update_fields(conn: sqlite3.Connection, job_id: str, **fields) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = [*fields.values(), datetime.now(UTC).isoformat(), job_id]
    conn.execute(f"UPDATE jobs SET {sets}, updated_at=? WHERE id=?", vals)
    conn.commit()


def _insert_chunk_rows(
    conn: sqlite3.Connection, job_id: str, chunks: list[str]
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO chunks "
        "(job_id, chunk_index, text, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?)",
        [(job_id, i, t, now, now) for i, t in enumerate(chunks)],
    )
    conn.commit()


async def _synthesize_all(
    conn: sqlite3.Connection,
    job_id: str,
    text_chunks: list[str],
    engine: TTSEngine,
    voice: str,
    speed: float,
    data_dir: Path,
) -> list[Path]:
    chunk_dir = job_audio_dir(data_dir, job_id)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    total = len(text_chunks)
    start = time.monotonic()
    counts = {"resolved": 0, "retries": 0, "failed": 0}
    last_emit = {"at": start, "resolved": 0}

    def _maybe_emit_progress() -> None:
        now = time.monotonic()
        enough_chunks = counts["resolved"] - last_emit["resolved"] >= PROGRESS_EVERY_CHUNKS
        enough_time = now - last_emit["at"] >= PROGRESS_EVERY_SEC
        if not (enough_chunks or enough_time):
            return
        elapsed = now - start
        rate = elapsed / max(counts["resolved"], 1)
        remaining = rate * (total - counts["resolved"])
        _log_event(
            conn, job_id, "generating",
            f"{counts['resolved']}/{total} segments "
            f"({counts['retries']} retries, {counts['failed']} failed) -- "
            f"~{_format_duration(remaining)} remaining",
        )
        last_emit["at"] = now
        last_emit["resolved"] = counts["resolved"]

    async def _synth_one(idx: int, text: str) -> Path:
        wav_path = chunk_dir / f"chunk_{idx:03d}.wav"

        def _on_retry(attempt: int, exc: Exception) -> None:
            counts["retries"] += 1

        try:
            wav = await engine.synthesize(text, voice, speed, on_retry=_on_retry)
            wav_path.write_bytes(wav)
            conn.execute(
                "UPDATE chunks SET status='done', audio_path=?, updated_at=? "
                "WHERE job_id=? AND chunk_index=?",
                (str(wav_path), datetime.now(UTC).isoformat(), job_id, idx),
            )
            conn.commit()
            counts["resolved"] += 1
            _maybe_emit_progress()
            logger.debug("chunk %d/%d synthesized", idx + 1, total)
            return wav_path
        except Exception as exc:
            conn.execute(
                "UPDATE chunks SET status='failed', error=?, updated_at=? "
                "WHERE job_id=? AND chunk_index=?",
                (str(exc), datetime.now(UTC).isoformat(), job_id, idx),
            )
            conn.commit()
            counts["resolved"] += 1
            counts["failed"] += 1
            _log_event(
                conn, job_id, "generating", f"chunk {idx} failed permanently: {exc}"
            )
            raise

    results = await asyncio.gather(
        *[_synth_one(i, t) for i, t in enumerate(text_chunks)],
        return_exceptions=True,
    )

    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        raise errors[0]

    return list(results)  # type: ignore[return-value]


def _build_extractor(input_type: str, config: AppConfig):
    if input_type == "raw_text":
        return RawTextExtractor()
    if input_type == "file":
        return FileExtractor()
    if input_type == "url":
        return WebExtractor(
            jina_fallback=config.extraction.jina_fallback,
            jina_api_key=config.extraction.jina_api_key,
        )
    if input_type == "rss":
        return RSSImportExtractor()
    raise ValueError(f"Unknown input_type: {input_type!r}")


def _build_publisher(
    config: AppConfig,
    data_dir: Path,
    *,
    on_progress: Callable[[str], None] | None = None,
):
    base_url = f"http://{config.server.host}:{config.server.port}"
    if config.publisher.type == "local":
        feed_config = CoreFeedConfig(
            title=config.feed.title,
            link=base_url,
            description=config.feed.description,
        )
        return LocalPublisher(data_dir, base_url, feed_config)
    if config.publisher.type == "github_pages":
        owner, _, repo_name = config.publisher.repo.partition("/")
        feed_config = CoreFeedConfig(
            title=config.feed.title,
            link=f"https://{owner}.github.io/{repo_name}",
            description=config.feed.description,
        )
        return GitHubPagesPublisher(
            repo=config.publisher.repo,
            token=config.publisher.token,
            work_dir=data_dir / "gh-pages",
            branch=config.publisher.branch,
            feed_config=feed_config,
            max_episodes=config.publisher.max_episodes,
            max_size_mb=config.publisher.max_size_mb,
            on_progress=on_progress,
        )
    raise ValueError(f"Unknown publisher type: {config.publisher.type!r}")


def _load_done_episodes(conn: sqlite3.Connection) -> list[Episode]:
    rows = conn.execute(
        "SELECT title, created_at, audio_duration_sec, source_url, public_url, file_size_bytes "
        "FROM jobs WHERE status='done' AND public_url IS NOT NULL AND public_url != '' "
        "ORDER BY created_at DESC"
    ).fetchall()
    episodes = []
    for row in rows:
        try:
            published = datetime.fromisoformat(row["created_at"])
        except ValueError:
            continue
        episodes.append(
            Episode(
                title=row["title"] or "Untitled",
                published=published,
                duration_sec=row["audio_duration_sec"] or 0.0,
                source_url=row["source_url"],
                public_url=row["public_url"],
                file_size_bytes=row["file_size_bytes"] or 0,
            )
        )
    return episodes
