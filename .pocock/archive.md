# Archive

Older iteration entries moved out of progress.md's rolling window.

---

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

### Setup (manual, pre-loop)
Scaffolded app shell + SQLite schema (closed reader-392/eng-T2, reader-tio/eng-T6
manually, not via loop):
- pyproject.toml: uv project, Python 3.12+, deps=[flask], dev=[pytest,ruff],
  entry point `audibleweb = audibleweb.app:main`
- audibleweb/db.py: migration runner, PRAGMA user_version, applies
  audibleweb/migrations/NNN_*.sql in order, idempotent
- audibleweb/migrations/001_initial.sql: jobs + chunks tables (docs/design.md sec 5;
  chunks table = per-chunk text intermediates per eng D5)
- audibleweb/app.py: create_app() factory runs migrations on startup, /healthz route,
  check_ffmpeg() exits clean if ffmpeg missing from PATH
- tests/test_db.py, tests/test_app.py: 6 tests passing

---

### Setup round 2 (manual, pre-loop)
Epic reader-8f2 expanded from 11 review-fixup tasks to 24 (11 fixups + 13 new
"build-*" base tasks covering plan Next Steps 2-10 + REST API/UI/SSE/config/
plugin discovery). Reason: the 11 fixup tasks target modules that don't exist
yet — base build must land first. Dependency graph wired via `bd dep` (verified
`bd dep cycles` = none), so fixups auto-unblock once their target module exists.

10 P1 tasks are ready now (no blockers) — run `bd ready --parent reader-8f2`:
- reader-tt4 [eng-T1] vendor lib/voice.py (from audiobook-creator, build split per D1)
- reader-z4v [eng-T3] background asyncio worker thread
- reader-8f2.1 [build-3] vendor core/cleaning.py
- reader-8f2.2 [build-4] vendor core/chunking.py — ALSO decide kokoro_text_normalization
  scope (see Project Learnings below, open decision)
- reader-8f2.3 [build-5] extractors/ (raw_text, file, web, rss)
- reader-8f2.4 [build-6] engines/kokoro.py
- reader-8f2.5 [build-7] pipeline/stitch.py (ffmpeg concat)
- reader-8f2.6 [build-8] publishers/github_pages.py + local.py
- reader-8f2.7 [build-1a] REST API (api/routes.py)
- reader-8f2.12 [build-10] pipeline/normalize.py (optional LLM stage)

Key blocking chains (fixups wait on these):
- reader-8f2.4 (kokoro) blocks reader-yau (WAV validation) + reader-n19 (pause/fallback)
- reader-8f2.3 (extractors) blocks reader-whv (RSS first-sync) + reader-8f2.13 (plugin discovery)
- reader-8f2.6 (publisher) blocks reader-fco (episode rotation) + reader-ksd (atomic push)
- reader-z4v (worker) blocks reader-ebs (async-arch doc)
- reader-8f2.7 (api) blocks reader-8f2.11 (sse) + reader-8f2.8 (web UI)
- reader-8f2.10 [build-9, P1] wires everything together (queue.py + SSE) — blocked by
  ALL of the above, by design. Do this last.

---

### Iteration: reader-z4v [eng-T3] background worker thread (closed)
Built `audibleweb/worker.py` (Worker: daemon thread + own asyncio event loop,
polls `jobs` for status='queued' one at a time, `start()`/`stop()` w/ graceful
shutdown) + `audibleweb/core/pipeline.py` (stub `run_pipeline(conn, job_id)` ->
sets status='done'). Wired into `create_app(start_worker=True default)` via
`app.extensions["worker"]` + `atexit.register(worker.stop)`.

Key decisions:
- Created `audibleweb/core/` package — this is the design.md sec 11
  orchestration package (pipeline.py, later job_queue.py/text_pipeline.py/
  feed.py), NOT the vendored-utils location. Confirms D2: vendored
  voice/cleaning/chunking code still goes in `lib/`, no collision.
- Added `PRAGMA busy_timeout = 5000` to db.py's get_connection — worker
  thread + Flask routes now hold separate connections to the same SQLite
  file; writers wait instead of raising "database is locked" on collision.
  Skipped WAL (avoids extra -wal/-shm files for v1).
- Worker.stop() must tolerate being called twice (atexit + manual in tests) —
  asyncio.run() closes the loop after _main() returns, so stop() checks
  `loop.is_closed()` before call_soon_threadsafe.
- core/pipeline.run_pipeline is the extension point for real
  extract->normalize->generate->publish stages (later build tasks, esp.
  reader-8f2.10). Currently just flips queued->done.
- Existing test_app.py tests pass `start_worker=False` (worker not under
  test there); added test_create_app_starts_worker for the wiring itself.

Files: audibleweb/worker.py (new), audibleweb/core/__init__.py (new),
audibleweb/core/pipeline.py (new), audibleweb/db.py (busy_timeout),
audibleweb/app.py (Worker wiring), tests/test_worker.py (new),
tests/test_app.py (start_worker flags + new test).

Unblocks: reader-8f2.10 (final wiring) can now build on Worker/run_pipeline;
reader-ebs (async-arch doc) can reference this implementation.

---

### Iteration: reader-8f2.2 [build-4] chunking + kokoro_text_normalization scope decision (closed)
Built `audibleweb/lib/chunking.py` (`chunk_text(text, level: "paragraph"|"sentence")
-> list[str]`) + `tests/test_chunking.py` (5 tests). Adapted from abogen's
core/chunking.py::chunk_text, stripped per acceptance criteria: no
chapter_index/speaker_id/voice_profile/voice_formula/build_chunks_for_chapters
(single-voice, no chapters per D10).

**Decision (kokoro_text_normalization.py scope, the open question):** NOT vendored
as part of chunking, and chunk_text does NOT call normalize_for_pipeline at all.
- AudibleWeb's pipeline order is extract->clean->normalize->pronunciation->chunk
  (design.md sec 3) — normalization happens upstream on the full text, so
  chunking is pure structural paragraph/sentence splitting on already-normalized
  text. Dropped abogen's normalized_text/display_text/original_text dual-tracking
  entirely (that existed only because abogen normalizes per-chunk, post-split).
- The bulk of kokoro_text_normalization.py (~2300 lines: dates/times/numbers/
  currency/roman-numerals/contractions/internet-slang/address-abbrev/acronyms/
  titles) is engine-agnostic "make raw text speakable" normalization, despite the
  filename. It's the rule-based default impl for pipeline/normalize.py
  (reader-8f2.12), as `lib/text_normalization.py`, slimmed: drop abogen's
  runtime-settings/cache layer (normalization_settings.py) in favor of
  config.yaml keys, and drop its narrow LLM-contraction sub-path (mode=="llm")
  — AudibleWeb's broader Stage-2 LLM rewrite (design.md sec 3) supersedes it.
  Always-on rule-based normalization runs regardless of whether the optional
  LLM stage is configured (matches "LLM normalization gracefully degrades" key
  decision).
- `apply_phoneme_hints` (IPA/misaki sibilant-iz markers, ~6 lines) IS genuinely
  Kokoro-specific — relocate to engines/kokoro.py as a pre-synthesis step
  (reader-8f2.4), not part of the shared pipeline.
- Recorded in reader-8f2.2 notes (`bd show reader-8f2.2`) so reader-8f2.12 doesn't
  re-derive this.

Files: audibleweb/lib/__init__.py (new), audibleweb/lib/chunking.py (new),
tests/test_chunking.py (new), .pocock/archive.md (new — moved oldest entry here).

Unblocks: reader-8f2.12 (normalize.py) has its scope/placement decided;
reader-8f2.4 (kokoro engine) has apply_phoneme_hints placement decided;
reader-8f2.10 (final wiring) — chunking dep satisfied.

**reader-8f2.4 revisited this call (see current iteration):** `apply_phoneme_hints`
turned out to be `text.replace(iz_marker, " iz")` -- a pure string substitution
with no TTS-client/voice dependency. Its only producer is `‹IZ›` markers inserted
by abogen's `normalize_apostrophes` (part of the not-yet-vendored
lib/text_normalization.py). Re-scoped to reader-8f2.12 as a final text-pipeline
step (end of normalize.py), NOT engines/kokoro.py -- avoids a dead/no-op stub in
kokoro.py until the marker-producing side exists.

### Iteration: reader-tt4 [eng-T1] lib/voice.py voice spec parsing + weighted mix (closed)
Built `audibleweb/lib/voice.py` (`parse_voice_spec`, `VoiceSpec`/`VoiceWeight`
dataclasses, `InvalidVoiceSpecError`, `mix_weighted_blend`) + `tests/test_voice.py`
(14 tests). Added `pydub` dep (`uv add pydub`, 0.25.1) — ffmpeg already on PATH
(/opt/homebrew/bin/ffmpeg), pydub uses it for WAV export/overlay.

Key decisions:
- Combined audiobook-creator's `parse_voice_string` + `validate_voice_string`
  into ONE `parse_voice_spec()` that validates first and RAISES
  `InvalidVoiceSpecError` (subclass of ValueError) on invalid input, returning
  a `VoiceSpec` dataclass (not a dict) on success — matches project convention
  (Article/Extractor dataclasses in extractors/base.py) and acceptance
  criteria's "raises clear error on invalid spec".
- `mix_weighted_blend(buffer_a, weight_a, buffer_b, weight_b) -> bytes` is the
  SPLIT half (D1): pure bytes-in/bytes-out, no asyncio/TTS client. Dropped
  audiobook-creator's `generate_weighted_mix` entirely (it called
  `generate_audio_with_retry` + did concurrent TTS gather) — that TTS-calling
  responsibility belongs to engines/kokoro.py (reader-8f2.4), which will call
  this mix function with the two already-generated WAV buffers. No
  pytest-asyncio needed since this module has zero async code.
- Kept exactly-2-voices signature for mix_weighted_blend (matches the
  weighted-blend validation rule: max 2 voices) rather than a generic
  list[bytes]/list[float] — simpler, no premature generality.
- Known pre-existing edge case carried over unchanged: validation allows an
  individual weight of 0.0 (since 0<=weight<=1 and e.g. "a:1.0+b:0.0" sums to
  1.0), but `_adjust_volume_by_weight` raises ValueError for weight<=0. Same
  inconsistency existed in audiobook-creator source; not fixed here (out of
  scope, no spec'd behavior for weight=0 — flag if reader-8f2.4 hits it).

Files: audibleweb/lib/voice.py (new), tests/test_voice.py (new, 14 tests),
pyproject.toml + uv.lock (+pydub).

Unblocks: reader-8f2.4 (engines/kokoro.py) can import parse_voice_spec +
mix_weighted_blend for blend resolution.

### Iteration: reader-8f2.3 [build-5] extractors/base.py + raw_text.py + file.py (closed)
Built `audibleweb/extractors/{base,raw_text,file}.py` + `tests/test_extractors.py`
(21 tests).

Key decisions:
- Scope split: original reader-8f2.3 covered all 4 extractors (raw_text/file/web/
  rss) — too big for one pass. Split web.py -> reader-8f2.14, rss.py ->
  reader-8f2.15 (both dep on this issue for base.py). Re-wired reader-8f2.10
  (final wiring) and reader-whv (RSS first-sync) to also dep on 8f2.14/8f2.15
  respectively (verified `bd dep cycles` = none). reader-8f2.3's own acceptance
  criteria narrowed to base.py+raw_text.py+file.py.
- extractors/base.py is the shared core abstraction for ALL 4 extractors (incl.
  8f2.14/8f2.15): `Article` dataclass, `Extractor` Protocol (runtime_checkable for
  plugin-discovery isinstance checks, reader-8f2.13), `ExtractionError` exception,
  `derive_title()` + `make_article()` factory. make_article() enforces the
  "<100 chars -> No extractable content" failure mode from design.md sec 9 —
  centralized here so 8f2.14 (web) and 8f2.15 (rss) don't reimplement it.
- RawTextExtractor.can_handle always returns True (catch-all/fallback — raw text
  is explicitly selected via input_type, never auto-detected). Note for
  reader-8f2.10/8f2.13: any can_handle-based dispatcher must check
  RawTextExtractor last.
- FileExtractor: .pdf via PyMuPDF (`fitz`, added as dep — `uv add pymupdf`,
  1.27.2.3). .md title derived from first "# heading" via derive_title(); .txt
  title = filename stem (txt rarely has a meaningful first line). PDF
  title/author pulled from doc.metadata, falls back to filename stem.
- Did NOT implement source-unreachable / both-methods-failed checks beyond
  make_article's generic <100-char check — web.py's specific failure messages
  ("Could not fetch URL", "Extraction failed (both methods)") are 8f2.14's job.

Files: audibleweb/extractors/{__init__,base,raw_text,file}.py (new),
tests/test_extractors.py (new, 21 tests), pyproject.toml + uv.lock (+pymupdf).

Unblocks: reader-8f2.14 (web.py) and reader-8f2.15 (rss.py) have base.py +
shared ExtractionError/make_article ready to import.

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
