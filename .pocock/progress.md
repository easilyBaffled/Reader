# Progress & Learnings

This file maintains context between autonomous iterations.
**READ THIS FIRST** to understand recent decisions and roadblocks.

---

## Recent Context (Last 3 Iterations)

<!-- This section is a rolling window - keep only the last 3 entries -->
<!-- Move older entries to archive.md -->

### Iteration: reader-fco [eng-T5] Episode rotation + pre-push MP3 size check (closed)
Added `_apply_rotation()` + `_check_audio_size()` to `GitHubPagesPublisher`.
2 new tests (175 total). No new deps.

Key decisions:
- `max_episodes: int = 0` (0=unlimited) + `max_size_mb: int = 0` (0=no check) added
  to constructor; production passes `config.publisher.max_episodes/max_size_mb`.
  `PublisherConfig` defaults: max_episodes=0, max_size_mb=900.
- `_apply_rotation`: sorts episodes by published desc, keeps newest N, `unlink()`s
  excess MP3s from `work_dir/audio/` (skips if file doesn't exist).
- `_check_audio_size`: sums `work_dir/audio/*.mp3` sizes; raises
  `GitHubPagesPublisherError` if > limit_bytes. Called AFTER _apply_rotation so
  rotation reduces size first.
- Both methods wired into `publish_and_update_feed()` AND `update_feed()`.
  Both run BEFORE `_commit_and_push` → remote always untouched on failure
  (all-or-nothing rule satisfied).
- Did NOT clean up copied MP3 from work_dir on size failure — remote untouched
  is the invariant, work_dir is just a local clone that can be re-cloned.

Files: audibleweb/publishers/github_pages.py (modified, +2 methods),
audibleweb/config.py (+max_episodes/max_size_mb to PublisherConfig),
config.yaml (+max_episodes/max_size_mb to publisher section),
tests/test_publishers.py (+2 tests).

### Iteration: reader-8f2.7.1 [build-1a sub] GET/PUT /api/settings endpoint (closed)
Added `GET /api/settings` + `PUT /api/settings` to `audibleweb/api/routes.py`.
10 new tests (173 total). No new deps (yaml already present via pyyaml).

Key decisions:
- `app.config["CONFIG_PATH"]` stores the resolved config path so the settings
  endpoint can write back config.yaml without hardcoding. `create_app()` gains
  an optional `config_path` param; defaults to `DEFAULT_CONFIG_PATH` from config.py.
  Tests pass `tmp_path / "config.yaml"` for full isolation.
- GET: `dataclasses.asdict(config)` then `_strip_secrets()` removes the 5
  secret fields (publisher.token, tts.api_key, extraction.jina_api_key,
  normalization.llm_api_key, server.api_key). Secrets live in .env only.
- PUT: body validated (must be dict, known sections only, each value a dict).
  Secrets stripped silently from incoming body. Merges into current raw yaml
  dict, validates merged result by constructing each affected section's
  dataclass — TypeError on unknown fields → 400. Writes yaml.safe_dump,
  calls load_config(), updates current_app.config["APP_CONFIG"].
- `_SECTION_CLASSES` + `_SECRET_FIELDS` are module-level dicts in routes.py
  to keep secret-stripping logic co-located with the endpoint.

Files: audibleweb/api/__init__.py (staged, pre-existing untracked),
audibleweb/api/routes.py (new/staged), audibleweb/app.py (modified,
+config_path param + CONFIG_PATH in app.config), tests/test_api.py (new/staged).

### Iteration: reader-ksd [ceo-T4] Atomic single-push publish workflow (closed)
Added `publish_and_update_feed(episode, audio_path, all_episodes) -> tuple[str, str]`
to Publisher Protocol in `base.py` + concrete implementations in both publishers.
3 new tests (14 publisher tests total, 163 suite total). No new deps.

Key decisions:
- Protocol gets a default `publish_and_update_feed()` body that calls `publish()`
  then `update_feed()` sequentially — Python Protocol allows method bodies, but
  concrete classes don't inherit them unless they subclass the Protocol. So both
  `LocalPublisher` and `GitHubPagesPublisher` implement the method explicitly.
- `GitHubPagesPublisher.publish_and_update_feed()`: `_ensure_clone()` once, copy
  MP3 to `work_dir/audio/`, generate+validate feed.xml, write to `work_dir/`,
  then ONE `_commit_and_push()`. If `validate_feed()` raises, no commit/push →
  gh-pages remote is untouched (crash-safe).
- `LocalPublisher.publish_and_update_feed()`: calls `publish()` + `update_feed()`
  sequentially — local file writes are already atomic enough (no git push).
- `all_episodes` argument is the FULL list including the new episode (caller
  builds it). The publisher does not mutate the episode list.
- Queue wiring (reader-8f2.10) MUST call `publish_and_update_feed()` instead of
  calling `publish()` then `update_feed()` separately — that's the broken pattern
  this issue fixes.

Files: audibleweb/publishers/base.py (modified), audibleweb/publishers/github_pages.py
(modified), audibleweb/publishers/local.py (modified), tests/test_publishers.py
(modified, +3 tests).

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
