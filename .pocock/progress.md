# Progress & Learnings

This file maintains context between autonomous iterations.
**READ THIS FIRST** to understand recent decisions and roadblocks.

---

## Recent Context (Last 3 Iterations)

<!-- This section is a rolling window - keep only the last 3 entries -->
<!-- Move older entries to archive.md -->

### Iteration: reader-8f2.11 [build-1c] SSE progress stream (closed)
New `audibleweb/api/sse.py` blueprint. 7 new tests (261 total). No new deps.

Key decisions:
- `sse_bp` registered at `/api/jobs` with route `/<job_id>/stream` (GET).
  Separate blueprint from `api_bp` to keep routes.py focused.
- Sync Flask generator via `stream_with_context` + `time.sleep(1)` polling SQLite.
  No async, no shared in-process state — matches D13 (Flask stays sync).
- Event payload: `{id, status, title, error, chunks_done, chunks_total}`. Only
  `chunks_done`/`chunks_total` are non-zero when `status=="generating"` (queried
  from `chunks` table via COUNT/SUM). Zero for all other stages.
- Generator terminates on `done`/`failed` (terminal set). Unknown job_id emits
  `{error: "job not found", id: ...}` and returns immediately.
- Queue tab JS: inline `<script>` in `queue.html` active-job card. `EventSource`
  opens on card render, updates progress-fill width + label text on each message.
  On terminal state: `es.close()` + `htmx.ajax` to reload `/tab/queue`.
- Progress pct formula: extracting=10, normalizing=30, generating=30+60*(done/total),
  publishing=90. Hard-coded stage map — simple enough, avoids DB writes.
- Tests: Flask test client reads full SSE body synchronously (generator exhausts
  when terminal). Non-terminal (generating) jobs tested via `_progress()` direct
  call (snapshot) to avoid infinite loop in test client.

Files: audibleweb/api/sse.py (new), audibleweb/app.py (+sse_bp import+register),
audibleweb/web/templates/partials/queue.html (+SSE JS on active card),
tests/test_sse.py (new, 7 tests).

### Iteration: reader-8f2.8 [build-1b] Web UI: Jinja templates + DESIGN.md tokens (closed)
New `audibleweb/web/` blueprint + `audibleweb/static/css/`. 25 new tests (254 total). No new deps.

Key decisions:
- Static files go in `audibleweb/static/css/` (app-level static), NOT a blueprint static
  folder. Blueprint `static_url_path='/static'` conflicts with Flask app-level `/static`
  route (app-level wins); app-level static dir resolves to `audibleweb/static/` correctly.
- `audibleweb/web/routes.py` = Blueprint("web") with `template_folder="templates"` only.
  Routes: `GET /` (index, Queue default), `GET /tab/<name>` (HTMX swap target, 4 tabs),
  `POST /web/jobs` (form → create job → return Queue partial).
- Jinja macros in `templates/macros.html`: `icon(name)` (13 Lucide SVG paths inline) +
  `status_badge(status)` (pill with color + icon by status). Used via `{% from %}` import.
- HTMX tab switching: `hx-get="/tab/{name}"` + `hx-target="#main-content"` on each tab
  link. Tab active state updated via `htmx:afterSettle` listener in base.html — tiny
  inline script, not a framework.
- Drag-drop overlay: full-page, JS dragenter/dragleave/drop handlers in base.html.
  `dragDepth` counter handles nested drag events correctly. Calls `htmx.ajax` for file drop.
- `_job_to_dict` imported from `api/routes.py` — web routes reuse API's stall-detection
  logic without duplicating it.

Files: audibleweb/web/__init__.py (new), audibleweb/web/routes.py (new),
audibleweb/web/templates/base.html, index.html, macros.html,
partials/queue.html, partials/inbox.html, partials/feed.html, partials/settings.html (all new),
audibleweb/static/css/tokens.css (new, exact from DESIGN.md sec1),
audibleweb/static/css/app.css (new), audibleweb/app.py (register web_bp),
tests/test_web_ui.py (new, 25 tests).

### Iteration: reader-whv [ceo-T2] RSS first-sync: mark existing items seen (closed)
New `audibleweb/migrations/003_rss_seen.sql`. 4 new tests (229 total). No new deps.

Key decisions:
- `rss_seen_items(feed_url, item_id, seen_at)` — PK (feed_url, item_id). item_id =
  `entry.id` (guid) → `entry.link` fallback. Entries with neither skipped from tracking
  (always treated as new — rare edge, spec says entries should have link).
- `get_seen_item_ids(conn, feed_url) -> set[str]` + `mark_items_seen(conn, feed_url,
  item_ids)` in `db.py`. `INSERT OR IGNORE` makes mark idempotent.
- `first_subscribe(feed_url, conn) -> int`: marks ALL current items seen, returns count.
  Queue wiring (reader-8f2.10) calls this on new feed subscription → 0 jobs created.
- `list_new_articles(feed_url, conn) -> list[Article]`: skips seen items, marks returned
  items seen before return. Short/failed entries: ID still marked seen (no retry).
- `list_articles()` unchanged — existing callers unaffected.
- test_db.py version assertions bumped 2→3 + added rss_seen_items to table check.

Files: audibleweb/migrations/003_rss_seen.sql (new), audibleweb/db.py (+2 helpers),
audibleweb/extractors/rss.py (+first_subscribe, +list_new_articles, +_entry_id),
tests/test_db.py (version bump), tests/test_rss_extractor.py (+4 tests).

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
