# Progress & Learnings

This file maintains context between autonomous iterations.
**READ THIS FIRST** to understand recent decisions and roadblocks.

---

## Recent Context (Last 3 Iterations)

<!-- This section is a rolling window - keep only the last 3 entries -->
<!-- Move older entries to archive.md -->

### Iteration: reader-8f2.4 [build-6] engines/base.py + engines/kokoro.py (closed)
Built `audibleweb/engines/{base,kokoro}.py` + `tests/test_kokoro.py` (10 tests,
58 total now). Added `httpx` dep (`uv add httpx`, 0.28.1) for async HTTP to the
Kokoro OpenAI-compatible `/v1` endpoint.

Key decisions:
- `TTSEngine.synthesize(text, voice, speed) -> bytes` (raw WAV), NOT
  design.md sec 2.2's `AudioSegment` — keeps the bytes-in/bytes-out contract
  lib/voice.py's `mix_weighted_blend` already established (reader-tt4), and
  lets pipeline/stitch.py (reader-8f2.5) write chunks straight to temp files
  for FFmpeg concat without pydub in the stitching path (CLAUDE.md: "FFmpeg
  for stitching, not PyDub"). Documented as an intentional deviation in
  engines/base.py's docstring.
- `list_voices() -> list[str]`, NOT `list[Voice]` — Kokoro's `/audio/voices`
  returns `{"voices": [...]}` (flat strings); no `Voice` dataclass needed
  until a second engine justifies one (YAGNI). Also documented in base.py.
- `_generate_with_retry` (D8): one shared retry/backoff helper used for (a) a
  native voice/blend (1 call, native_string passed straight through to the
  API) and (b) each leg of a weighted blend (2 calls), whose results
  `lib.voice.mix_weighted_blend` then mixes. Retry params match
  audiobook-creator's contract exactly (4 total attempts, 0.1/0.2/0.4s
  backoff + 0-10% jitter, no error discrimination — any exception retried).
- Timeout = 120s/chunk (docs/design.md sec 9's "TTS | Timeout (120s/chunk) |
  Count as failure, retry"), NOT audiobook-creator's vendored 600s — a
  deliberate AudibleWeb-specific value, not carried over from the source.
- Semaphore (`asyncio.Semaphore(max_parallel)`) lives inside
  `_generate_with_retry` itself, so it bounds the actual concurrent
  `/audio/speech` calls regardless of caller — a weighted blend's 2 legs each
  acquire a slot independently. `max_parallel` is a plain constructor int
  (config.yaml `tts.max_parallel`, default 4) — config.py (reader-8f2.9) just
  needs to read the value and pass it in; no coupling added here.
- Used `httpx.AsyncClient` + `httpx.MockTransport` directly (no `openai` SDK
  dep) — `/audio/voices` isn't part of the OpenAI schema, so httpx covers both
  endpoints with one dependency and makes the "mock TTS server fixture" trivial
  (no real server/port needed, just a request handler returning silence WAV /
  a voices JSON body). `KokoroEngine(..., client=...)` accepts an injected
  client for this.
- **Resolved reader-8f2.2's open `apply_phoneme_hints` placement question**:
  it's `text.replace(iz_marker, " iz")` — pure string substitution, no
  TTS-client/voice dependency. Its only producer (`‹IZ›` markers from abogen's
  `normalize_apostrophes`) doesn't exist yet (part of not-yet-vendored
  lib/text_normalization.py). Re-scoped to reader-8f2.12 as a final
  text-pipeline step in normalize.py, NOT engines/kokoro.py — avoids a dead
  no-op stub here. See archive.md for the fuller note.

Files: audibleweb/engines/{__init__,base,kokoro}.py (new),
tests/test_kokoro.py (new, 10 tests), pyproject.toml + uv.lock (+httpx).

Unblocks: reader-8f2.10 (queue.py wiring) — TTSEngine Protocol + KokoroEngine
ready to import. reader-yau (WAV header validation) and reader-n19
(pause/weighted-blend fallback) can now reference `_generate_with_retry` /
`synthesize`'s shape.

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

### Vendoring sources (local paths, confirmed to exist)
- `/Users/Daniel.Michaelis/abogen/abogen/chunking.py` — source for reader-8f2.2
  (done — see closed iteration above).
- `/Users/Daniel.Michaelis/abogen/abogen/{kokoro_text_normalization.py,
  normalization_settings.py,llm_client.py}` — source for reader-8f2.12
  (lib/text_normalization.py, slimmed per reader-8f2.2's scope decision above);
  `apply_phoneme_hints` portion goes to reader-8f2.4 (engines/kokoro.py) instead.
- `/Users/Daniel.Michaelis/abogen/abogen/word_substitution.py` — source for
  reader-8f2.1 (lib/cleaning.py).
