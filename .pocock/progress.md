# Progress & Learnings

This file maintains context between autonomous iterations.
**READ THIS FIRST** to understand recent decisions and roadblocks.

---

## Recent Context (Last 3 Iterations)

<!-- This section is a rolling window - keep only the last 3 entries -->
<!-- Move older entries to archive.md -->

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

### Vendoring sources (local paths, confirmed to exist)
- `/Users/Daniel.Michaelis/audiobook-creator/utils/run_shell_commands.py` +
  matching `tests/` — source for reader-8f2.5 (pipeline/stitch.py ffmpeg
  helpers). (voice_parser.py/audio_mixer.py already vendored — see reader-tt4
  closed iteration above.)
- `/Users/Daniel.Michaelis/abogen/abogen/chunking.py` — source for reader-8f2.2
  (done — see closed iteration above).
- `/Users/Daniel.Michaelis/abogen/abogen/{kokoro_text_normalization.py,
  normalization_settings.py,llm_client.py}` — source for reader-8f2.12
  (lib/text_normalization.py, slimmed per reader-8f2.2's scope decision above);
  `apply_phoneme_hints` portion goes to reader-8f2.4 (engines/kokoro.py) instead.
- `/Users/Daniel.Michaelis/abogen/abogen/word_substitution.py` — source for
  reader-8f2.1 (lib/cleaning.py).
