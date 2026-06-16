# Progress & Learnings

This file maintains context between autonomous iterations.
**READ THIS FIRST** to understand recent decisions and roadblocks.

---

## Recent Context (Last 3 Iterations)

<!-- This section is a rolling window - keep only the last 3 entries -->
<!-- Move older entries to archive.md -->

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

### Iteration: reader-8f2.12 [build-10] pipeline/normalize.py: optional LLM Stage 2 (closed)
Built `audibleweb/pipeline/normalize.py` + `tests/test_normalize.py` (17 tests,
160 total now). No new deps (httpx already present).

Key decisions:
- `normalize_text(text, config, *, _client=None) -> str` — async, same
  constructor-injection pattern as KokoroEngine/WebExtractor. `_client`
  (httpx.AsyncClient) injected for tests; prod path creates its own client
  per call and closes it in a `finally` block.
- `_is_configured(config)` → False if `llm_enabled=False` or `llm_base_url=""`
  or `llm_model=""` — any of these means "skip silently, return original".
- Chunking: `_chunk_text(text, max_chars=2000)` splits on `\n\n`, groups
  paragraphs up to max_chars (separator costs 2 chars), flushes when next
  para would overflow. Single para longer than max_chars stays as one chunk
  (never mid-paragraph split). 2000 chars ≈ 500 tokens, fits typical LLM
  context easily.
- `_normalize_chunk`: POST to `{base_url}/v1/chat/completions` (strips
  trailing `/v1` duplication). Any exception (HTTP error, JSON decode, etc.)
  → `logger.warning(...)` and return original chunk. Empty/whitespace-only
  LLM response → same fallback. Malformed response shape (missing keys) →
  same. This satisfies design.md sec 9: "LLM unavailable → skip, continue"
  and "LLM returns garbage → discard, use pre-normalization text".
- System prompt verbatim from design.md sec 3: "Rewrite for spoken narration.
  Expand abbreviations, spell out numbers, don't change meaning."
- Auth: `Authorization: Bearer {api_key}` omitted when key="" or "ollama",
  matching kokoro.py and abogen/llm_client.py convention.
- `_build_url`: handles both `http://host/v1` (appends `/chat/completions`)
  and `http://host` (appends `/v1/chat/completions`) — avoids double `/v1/v1/`
  path for Ollama-style base URLs that already include `/v1`.
- Did NOT vendor `kokoro_text_normalization.py` (2378 lines, heavy deps:
  num2words, spacy). Design.md Stage 2 scope is LLM-only normalization;
  Stage 1 (rule-based Unicode/cleaning) is lib/cleaning.py (reader-8f2.1,
  already done). No new deps needed.

Files: audibleweb/pipeline/normalize.py (new), tests/test_normalize.py (new,
17 tests). No pyproject.toml changes.

Unblocks: reader-8f2.10 (queue wiring) — all three pipeline pieces now ready:
`stitch_chunks` (stitch.py), `normalize_text` (normalize.py), and config
(`load_config()`). reader-8f2.15 (rss.py extractor) still in_progress;
queue wiring depends on both.

### Iteration: reader-yau [ceo-T1] WAV header validation in engines/kokoro.py (closed)
Added `InvalidWAVError` + `_validate_wav_header(data: bytes) -> None` to
`audibleweb/engines/kokoro.py`. Added 6 new tests (143 total). No new deps.

Key decisions:
- `_validate_wav_header` is a module-level function (not a method) so tests can
  import and call it directly — same pattern as `_run_trafilatura` in web.py.
- Validation: `len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE"`.
  Minimum valid WAV header is 12 bytes (RIFF + 4-byte size + WAVE).
- Called inside the `try` block of `_generate_with_retry` after `response.content`,
  so `InvalidWAVError` propagates to `except Exception as exc` → retries (3x)
  → `KokoroEngineError` on exhaustion. No special-casing needed; existing retry
  loop handles it identically to HTTP errors.
- `InvalidWAVError` exported from kokoro.py (not engines/base.py) — it's engine-
  specific. Queue layer (reader-8f2.10) catches `KokoroEngineError` and annotates
  with chunk index per design.md sec 4; no change needed there.

Files: audibleweb/engines/kokoro.py (modified), tests/test_kokoro.py (modified,
+6 tests: 3 direct `_validate_wav_header` unit tests + 3 end-to-end retry tests).

### Iteration: reader-8f2.14 [build-5b] extractors/web.py: trafilatura + Jina fallback (closed)
Built `audibleweb/extractors/web.py` + `tests/test_web_extractor.py` (14 tests,
137 total now). Added `trafilatura==2.1.0` dep (`uv add trafilatura`). httpx
already present.

Key decisions:
- `WebExtractor(jina_fallback=True, jina_api_key="", _client=None)` — same
  constructor-injection pattern as KokoroEngine. `_client` (httpx.AsyncClient)
  injected for tests; both the HTML fetch and Jina call share the same client
  via `_get()` helper so tests mock with one MockTransport.
- Fetch strategy: httpx.AsyncClient owns the initial HTML GET (not
  `trafilatura.fetch_url()`). Reason: httpx raises `httpx.HTTPError` on
  connection/HTTP errors, giving a clean path to "Could not fetch URL".
  `trafilatura.fetch_url()` swallows all exceptions and returns None — no way
  to distinguish unreachable URL from extractable-but-empty page.
- Trafilatura called with `output_format="python"` → returns a Document object
  with `.text`, `.title`, `.author`, `.date` (str ISO date). Accessed via
  `getattr` defensively. If result is None OR text < 100 chars → Jina fallback.
- Jina endpoint: `https://r.jina.ai/{url}`, `Accept: text/plain`,
  `Authorization: Bearer {key}` (omitted if no key). httpx.HTTPError from
  Jina → "Extraction failed (both methods)"; text < 100 chars → same.
- Failure messages exactly match design.md sec 9: "Could not fetch URL" /
  "No extractable content" / "Extraction failed (both methods)".
- `_run_trafilatura(html, url)` is a module-level function (not a method) so
  `unittest.mock.patch("trafilatura.extract", ...)` patches at the call site
  cleanly in tests.

Files: audibleweb/extractors/web.py (new), tests/test_web_extractor.py (new,
14 tests), pyproject.toml + uv.lock (+trafilatura).

Unblocks: reader-8f2.15 (rss.py) still needed before reader-8f2.10 (queue
wiring). reader-8f2.15 is the last extractor — both block queue.

### Iteration: reader-8f2.9 [build-2x] config.py: .env + config.yaml hierarchy (closed)
Built `audibleweb/config.py` + `config.yaml` + `.env.example` +
`tests/test_config.py` (6 tests, 91 total now). Added `pyyaml`+`python-dotenv`
deps (`uv add pyyaml python-dotenv`).

Key decisions:
- `AppConfig` dataclass tree mirrors docs/design.md sec 8's config.yaml shape
  exactly (7 sections: feed/voice/tts/publisher/extraction/normalization/
  server), one dataclass per section, each field defaulted to sec 8's
  documented default. `load_config(config_path=, env_path=) -> AppConfig`:
  missing config.yaml -> all-defaults AppConfig (no error); present sections
  partial-merge over per-field defaults (`SectionConfig(**raw.get("section")
  or {})`), so a config.yaml with only `feed.title` set still gets
  `feed.description`'s default etc.
- Added 5 secret fields NOT in sec 8's yaml shape (`publisher.token`,
  `tts.api_key`, `extraction.jina_api_key` already in yaml shape but
  env-overridable, `normalization.llm_api_key`, `server.api_key`) — these come
  from `.env` via `_ENV_OVERRIDES` (env var -> (section, attr)), `.env` always
  wins over a config.yaml value if both set. Matches CLAUDE.md ".env (secrets)
  -> config.yaml (settings)" and sec 8's ".env -- secrets (GitHub PAT, API
  keys, LLM endpoint)" / sec 6 "GitHub PAT stored in .env, scoped to repo".
  `.env.example` documents all 5 vars (GITHUB_PAT, JINA_API_KEY,
  KOKORO_API_KEY, LLM_API_KEY, API_KEY).
- Committed root `config.yaml` = sec 8's example verbatim (the literal default
  config, not a template) — `load_config()`'s default `config_path` is
  `Path("config.yaml")` (cwd-relative, matches app.py's `DEFAULT_DB_PATH =
  Path("data/audibleweb.db")` cwd-relative convention).
- `load_dotenv(env_path, override=False)` — real env vars (e.g. CI secrets)
  win over `.env` file contents; `.env` only fills gaps. `override=False` is
  dotenv's default-safe choice for this.
- **Re-scoped acceptance criterion 4** ("GET/PUT /api/settings reads/writes
  via config.py") out of this issue — api/routes.py doesn't exist yet
  (reader-8f2.7 is still open/ready). Split into new reader-8f2.7.1 (parented
  under reader-8f2.7, depends-on this issue), with a note that the endpoint
  must exclude the 5 secret fields above from both GET response and
  config.yaml writes (secrets live in .env only, never round-tripped through
  config.yaml).

Files: audibleweb/config.py (new), config.yaml (new), .env.example (new),
tests/test_config.py (new, 6 tests), pyproject.toml + uv.lock
(+pyyaml+python-dotenv).

Unblocks: reader-8f2.10 (queue wiring) can now call `load_config()` to build
`KokoroEngine(base_url=config.tts.base_url, max_parallel=config.tts.
max_parallel, api_key=config.tts.api_key, ...)`,
`GitHubPagesPublisher(repo=config.publisher.repo, token=config.publisher.
token, ...)` / `LocalPublisher`, and `FeedConfig` (core/feed.py) from
`config.feed`. reader-8f2.7.1 (new) ready for reader-8f2.7's api/routes.py.

### Iteration: reader-8f2.6 [build-8] publishers/{base,local,github_pages}.py + core/feed.py (closed)
Built `audibleweb/publishers/{__init__,base,local,github_pages}.py` +
`audibleweb/core/feed.py` + `tests/test_feed.py` (10 tests) +
`tests/test_publishers.py` (12 tests). 81 total now. No new deps.

Key decisions:
- `publishers/base.py` is the shared core abstraction (like extractors/base.py
  and engines/base.py): `Publisher` Protocol (`publish(episode, audio_path) ->
  str`, `update_feed(episodes) -> str`, signatures verbatim from design.md sec
  2.3) + `Episode` dataclass + `episode_slug(title, published)` helper
  ("YYYY-MM-DD-sanitized-title", falls back to just the date if title has no
  alnum chars). `Episode` fields are exactly what feed.xml's <item> needs
  (title, published, duration_sec, source_url, public_url, file_size_bytes) —
  `public_url`/`file_size_bytes` default to ""/0 and are expected to be filled
  in by the caller (future reader-8f2.10 queue wiring) from `publish()`'s
  return value + the stitched MP3's file size before `update_feed()` is called.
- `core/feed.py` (per design.md sec 11 project structure) holds
  `FeedConfig` dataclass + `generate_feed(episodes, config) -> str` (RSS 2.0 +
  itunes namespace via `ET.register_namespace`, sorted newest-first, RFC822
  `pubDate` via `email.utils.format_datetime(..., usegmt=True)`) +
  `validate_feed(xml) -> None` (raises `FeedValidationError`). Both
  `publishers/local.py` and `publishers/github_pages.py` import from here —
  avoids duplicating feed-gen between the two publishers.
- `validate_feed` is STRUCTURAL validation only (well-formed XML + required
  <rss version="2.0">/<channel> elements + each <item> has
  title/enclosure(url,length,type)/guid/pubDate/description/itunes:duration) —
  not a full RSS XSD. No new dep (feedgen/lxml) added; ElementTree +
  hand-rolled checks satisfy "Validates against RSS 2.0 + iTunes podcast spec
  before push" (sec 6/9) without pulling in an external schema file or network
  fetch. Flag if a future issue needs stricter XSD validation.
- `GitHubPagesPublisher`: maintains ONE shallow clone of `gh-pages` in
  `work_dir` for the publisher's lifetime (`_ensure_clone` checks `.git`
  exists, no re-clone). `publish()` and `update_feed()` each independently
  `git add -A` + commit + `push --force origin <branch>` (force per sec 9:
  "gh-pages is generated content") — `_commit_and_push` no-ops (skips
  commit+push) if `git status --porcelain` is empty, so calling
  `update_feed()` twice with unchanged episodes doesn't error on "nothing to
  commit". Git ops via `asyncio.create_subprocess_exec("git", *args, ...)` —
  argv list (no shell), so no injection risk. Errors raise
  `GitHubPagesPublisherError` with the PAT redacted from any git stderr
  (`_redact`).
- Auth: `remote_url` defaults to `https://{token}@github.com/{repo}.git` but
  is constructor-overridable — tests pass a local bare-repo path as
  `remote_url` (no live network, per CLAUDE.md testing). Known limitation
  (documented in github_pages.py docstring, not handled): the `gh-pages`
  branch must already exist on the remote; bootstrapping a brand-new branch is
  a manual one-time setup step, out of scope here.
- `LocalPublisher` is plain file I/O (shutil.copy2 into `data_dir/audio/`,
  write `feed.xml` into `data_dir/`) — no git, matches "(no git)" in
  acceptance criteria.
- config.py (reader-8f2.9) is still open, so both publisher constructors take
  explicit args (repo/token/work_dir/branch/feed_config for github_pages;
  data_dir/base_url/feed_config for local) — same pattern as KokoroEngine
  (reader-8f2.4). reader-8f2.9 just needs to read config.yaml's `publisher:`
  block + `.env`'s GitHub PAT and pass them through.

Files: audibleweb/publishers/{__init__,base,local,github_pages}.py (new),
audibleweb/core/feed.py (new), tests/test_feed.py (new, 10 tests),
tests/test_publishers.py (new, 12 tests).

Unblocks: reader-8f2.10 (queue wiring), reader-fco (episode rotation), reader-ksd
(atomic single-push) — Publisher Protocol + Episode + both publishers + feed
gen/validation ready to import.

### Iteration: reader-8f2.5 [build-7] pipeline/stitch.py (FFmpeg concat) (closed)
Built `audibleweb/pipeline/{__init__,stitch.py}` + `tests/test_stitch.py` (4
tests). 85 total now. No new deps (ffmpeg/ffprobe already on PATH).

Key decisions:
- New `pipeline/` package (not `core/`) — matches the naming used by other
  open issues (reader-8f2.10 "pipeline/queue.py", reader-8f2.12
  "pipeline/normalize.py"), diverging from design.md sec11's `core/tts.py`.
  First file in this package.
- `stitch_chunks(chunk_paths: list[Path], output_path: Path) -> float`: single
  ffmpeg invocation using `-filter_complex` with per-stream `aformat` (forces
  s16/sample_rate/channel_layout from chunk[0]) followed by `concat`. This
  normalizes silence + all chunks to one format before concatenating, so
  ffmpeg's concat filter doesn't choke on mismatched sample rates/formats
  across chunks (tested explicitly: 24000Hz + 22050Hz chunks). 0.5s
  `anullsrc` silence prepended/appended via `-f lavfi -t 0.5 -i
  "anullsrc=r=...:cl=..."`, matched to chunk[0]'s rate/channel layout.
- Output: `-c:a libmp3lame -b:a 128k` (CBR per design.md sec4). Duration read
  back via `ffprobe -show_entries format=duration` on the encoded MP3 (not
  the input WAVs) — captures any encoder padding.
- chunk[0]'s sample_rate/channels read via stdlib `wave` module (no subprocess
  needed for that probe) — only ffmpeg/ffprobe calls go through
  asyncio.create_subprocess_exec (Eng D4: hardcoded argv, no shell, no
  allowlist needed beyond that — matches publishers/github_pages.py's git
  subprocess pattern).
- `StitchError` raised for: empty chunk_paths, ffmpeg non-zero exit, ffprobe
  non-zero exit.
- Tests use stdlib `wave` to generate silent WAV fixtures on the fly (no
  fixture files needed) — ffmpeg actually runs (no mock), same
  "tools-on-PATH" pattern as github_pages.py's local-bare-repo tests.

Files: audibleweb/pipeline/{__init__,stitch.py} (new), tests/test_stitch.py
(new, 4 tests).

Unblocks: reader-8f2.10 (queue.py wiring) — `stitch_chunks()` ready to import
for the generate->stitch->publish handoff.

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
