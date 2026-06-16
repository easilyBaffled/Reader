# Progress & Learnings

This file maintains context between autonomous iterations.
**READ THIS FIRST** to understand recent decisions and roadblocks.

---

## Recent Context (Last 3 Iterations)

<!-- This section is a rolling window - keep only the last 3 entries -->
<!-- Move older entries to archive.md -->

### Iteration: reader-8f2.15 [build-5c] extractors/rss.py: RSS feed import (closed)
Also closed: reader-8f2.7 (REST API) and reader-8hb (heartbeat) — both were already
fully implemented in prior iterations, just not marked done.

New `audibleweb/extractors/rss.py`. 15 new tests (225 total). Added feedparser dep.

Key decisions:
- `RSSImportExtractor.list_articles(feed_url) -> list[Article]` is the primary API
  (not `extract()`) — RSS is multi-article. `extract()` returns first article for
  Protocol compliance; queue wiring (reader-8f2.10) should call `list_articles()`.
- `can_handle()` heuristic: URL starts with http/https AND contains an RSS/feed/atom
  URL pattern (/rss, /feed, /atom, .xml, etc). No network call for routing.
- Fetch via httpx async, parse via `feedparser.parse(content_string)` (not feedparser's
  own fetch — keeps async flow consistent with other extractors).
- HTML stripping: `re.sub(r"<[^>]+>", " ", html)` — lightweight, no extra dep.
  feedparser already sanitizes most HTML; this is a final safety net.
- `content:encoded` preferred over `<description>` when present — full article body
  vs summary. `entry.get("content")[0]["value"]` for content:encoded via feedparser.
- Short entries (summary < 100 chars after strip) silently skipped via make_article
  raising ExtractionError — consistent with other extractors' min content policy.
- Tests: inline XML fixture strings + httpx.MockTransport; no network, no temp files.

Files: audibleweb/extractors/rss.py (new), tests/test_rss_extractor.py (new, 15 tests),
pyproject.toml (+feedparser>=6.0.12).

### Iteration: reader-8f2.1 [build-3] Vendor lib/cleaning.py Stage 1+3 text cleaning (closed)
New `audibleweb/lib/cleaning.py`. 11 new tests (210 total). No new deps.

Key decisions:
- `clean_text(text)` = Stage 1: fix non-standard punctuation (curly quotes, ellipsis) + ALL CAPS → lowercase.
  No numeral conversion — no `num2words` dep, Kokoro handles digits fine, LLM normalization step also helps.
- `apply_pronunciation_overrides(text, pronunciation)` = Stage 3: whole-word case-insensitive
  substitution from pronunciation.json flat dict `{word: replacement}`. Called separately in pipeline
  after LLM normalization.
- `split_text_preserving_markers` dropped entirely (D10 — single-voice, no chapter/voice markers).
- No new deps needed; stdlib `re` only.

Files: audibleweb/lib/cleaning.py (new), tests/test_cleaning.py (new, 11 tests).

### Iteration: reader-lvy [ceo-T6] Cleanup orphaned audio chunks on delete/final-fail (closed)
New `audibleweb/pipeline/queue.py`. 2 new tests (199 total). No new deps.

Key decisions:
- Chunk dir convention: `data_dir / "jobs" / job_id`. `data_dir` derived as `Path(db_path).parent`
  in both routes.py and worker.py — keeps dir co-located with DB.
- `cleanup_job_audio(data_dir, job_id)` → `shutil.rmtree` if dir exists (idempotent).
- `fail_job(conn, job_id, error, data_dir)` → UPDATE status='failed' + cleanup in one call.
  Called from worker.py's `_run_with_heartbeat` except block. Also changed: worker no longer
  re-raises on pipeline failure (was killing the worker loop); now marks failed + continues.
- Routes.py `delete_job` calls `cleanup_job_audio` before DELETE, AFTER mp3 unlink.

Files: audibleweb/pipeline/queue.py (new), audibleweb/api/routes.py (+cleanup call),
audibleweb/worker.py (+fail_job import, +data_dir param, except→fail_job not re-raise),
tests/test_api.py (+chunk dir cleanup test), tests/test_worker.py (+fail_job test).

- `plugins/{extractors,engines,publishers}/` created with `.gitkeep` to track in git.

Files: audibleweb/plugins.py (new), audibleweb/app.py (+PluginRegistry load, +plugins_dir param),
plugins/extractors/.gitkeep, plugins/engines/.gitkeep, plugins/publishers/.gitkeep (new),
tests/test_plugins.py (new, 12 tests).

---

## Active Roadblocks

<!-- No current roadblocks -->

---

## Project Learnings

Patterns, gotchas, and decisions that affect future work:

### Conventions established by the scaffold
- DB path: `data/audibleweb.db` (gitignored), override via `AUDIBLEWEB_DB_PATH` env var
  — use this for tests via `create_app(db_path=tmp_path / "test.db")`.
- New schema changes = new `audibleweb/migrations/00N_description.sql` file, NOT edits
  to 001_initial.sql. db.migrate() picks up anything with version > current
  PRAGMA user_version.
- Eng D2: vendored TTS-pipeline utilities go in `audibleweb/lib/` (not `core/`) to
  avoid a naming collision with docs/design.md's existing `core/` module names
  (core/pipeline.py, core/job_queue.py etc. per docs/design.md sec 11). Don't create
  a top-level `core/` package for vendored voice/cleaning/chunking code — use `lib/`.
- Eng D13: Flask app stays fully sync. Background work happens in a separate worker
  thread with its own asyncio event loop (reader-z4v/eng-T3). Routes never await —
  they read/write the `jobs`/`chunks` SQLite tables and the worker thread polls them.
- Feedback loop is wired and green: `uv run pytest`, `uv run ruff check .`,
  `uv run ruff format --check .` — keep all three passing before every commit.
- `audibleweb/extractors/base.py` (reader-8f2.3) is THE shared core abstraction
  for all 4 extractors: `Article`, `Extractor` Protocol, `ExtractionError`,
  `derive_title()`, `make_article()`. reader-8f2.14 (web.py) and reader-8f2.15
  (rss.py) should import from here, not redefine. PyMuPDF (`fitz`) added via
  `uv add pymupdf` — that pattern (uv add updates pyproject.toml + uv.lock
  together) works fine for adding new extractor/engine deps going forward
  (trafilatura/httpx/feedparser for 8f2.14/8f2.15).
- `audibleweb/lib/voice.py` (reader-tt4) is ready for reader-8f2.4 (kokoro
  engine): `parse_voice_spec(str) -> VoiceSpec` (raises `InvalidVoiceSpecError`)
  + `mix_weighted_blend(buffer_a, weight_a, buffer_b, weight_b) -> bytes`. Added
  `pydub` dep (`uv add pydub`); ffmpeg already on PATH so pydub WAV export/overlay
  works out of the box.
- `audibleweb/engines/{base,kokoro}.py` (reader-8f2.4) is ready for reader-8f2.10
  (queue.py wiring): `TTSEngine` Protocol (`synthesize(text, voice, speed) ->
  bytes` raw WAV, `list_voices() -> list[str]`) + `KokoroEngine(base_url, model=,
  api_key=, max_parallel=, client=)`. `KokoroEngine.synthesize` raises
  `InvalidVoiceSpecError` (from lib/voice.py) for bad voice specs and
  `KokoroEngineError` after retries exhaust. Added `httpx` dep (`uv add httpx`)
  — mock TTS in tests via `httpx.MockTransport`, no real server needed
  (tests/test_kokoro.py pattern). `apply_phoneme_hints` is NOT in kokoro.py —
  re-scoped to reader-8f2.12 (normalize.py), see that section + archive.md.
- `audibleweb/publishers/{base,local,github_pages}.py` + `audibleweb/core/feed.py`
  (reader-8f2.6) ready for reader-8f2.10 (queue wiring): `Publisher` Protocol +
  `Episode` dataclass + `episode_slug()` in publishers/base.py;
  `FeedConfig`/`generate_feed()`/`validate_feed()` in core/feed.py;
  `LocalPublisher(data_dir, base_url, feed_config)` and
  `GitHubPagesPublisher(repo, token, work_dir, branch=, feed_config=,
  remote_url=)`. Caller fills `Episode.public_url`/`file_size_bytes` from
  `publish()`'s return + the MP3's `stat().st_size` before calling
  `update_feed()`. Test gh-pages pushes against a local bare repo
  (`git init --bare -b gh-pages`) via `remote_url=` override — no live network.
- `audibleweb/pipeline/stitch.py` (reader-8f2.5) ready for reader-8f2.10 (queue
  wiring): `stitch_chunks(chunk_paths: list[Path], output_path: Path) ->
  float` (returns duration_sec). New `pipeline/` package — put
  reader-8f2.10's queue.py and reader-8f2.12's normalize.py here too (not
  `core/`), matching the issue titles' naming, not design.md sec11's
  `core/tts.py`/`core/pipeline.py` (core/pipeline.py already exists as the
  reader-z4v stub and stays — `pipeline/` is for the new per-stage modules).
  Raises `StitchError`.
- `audibleweb/config.py` (reader-8f2.9) is ready for reader-8f2.10 (queue
  wiring) and reader-8f2.7.1 (settings endpoint): `load_config(config_path=
  Path("config.yaml"), env_path=Path(".env")) -> AppConfig` with sections
  `feed/voice/tts/publisher/extraction/normalization/server` matching
  docs/design.md sec 8. Secrets (`publisher.token`, `tts.api_key`,
  `extraction.jina_api_key`, `normalization.llm_api_key`, `server.api_key`)
  come from `.env` (`_ENV_OVERRIDES`), never from config.yaml in practice —
  any future code that serializes `AppConfig` back to config.yaml (e.g.
  reader-8f2.7.1's PUT /api/settings) MUST exclude these 5 fields.

- `audibleweb/pipeline/normalize.py` (reader-8f2.12) ready for queue wiring:
  `normalize_text(text, config, *, _client=None) -> str` (async). Skip if
  `config.llm_enabled=False` or `base_url=""` or `model=""`. Any LLM error →
  returns original text (never raises). Chunks by paragraph (2000 chars).
  Imports from `audibleweb.config.NormalizationConfig`. No new deps.

- `audibleweb/extractors/web.py` (reader-8f2.14) ready for queue wiring:
  `WebExtractor(jina_fallback=True, jina_api_key="", _client=None)`. httpx for
  HTML fetch (not trafilatura.fetch_url — httpx gives clean HTTPError for
  "Could not fetch URL"). trafilatura.extract(..., output_format="python") for
  extraction. Jina Reader: `https://r.jina.ai/{url}`. Tests mock via
  httpx.MockTransport + `unittest.mock.patch("trafilatura.extract")`.

### Vendoring sources (local paths, confirmed to exist)
- `/Users/Daniel.Michaelis/abogen/abogen/chunking.py` — source for reader-8f2.2
  (done — see closed iteration above).
- `/Users/Daniel.Michaelis/abogen/abogen/{kokoro_text_normalization.py,
  normalization_settings.py,llm_client.py}` — source for reader-8f2.12
  (lib/text_normalization.py, slimmed per reader-8f2.2's scope decision above);
  `apply_phoneme_hints` portion goes to reader-8f2.4 (engines/kokoro.py) instead.
- `/Users/Daniel.Michaelis/abogen/abogen/word_substitution.py` — source for
  reader-8f2.1 (lib/cleaning.py).
