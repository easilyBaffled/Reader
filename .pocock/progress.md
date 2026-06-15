# Progress & Learnings

This file maintains context between autonomous iterations.
**READ THIS FIRST** to understand recent decisions and roadblocks.

---

## Recent Context (Last 3 Iterations)

<!-- This section is a rolling window - keep only the last 3 entries -->
<!-- Move older entries to archive.md -->

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

### Vendoring sources (local paths, confirmed to exist)
- `/Users/Daniel.Michaelis/audiobook-creator/utils/{voice_parser.py,audio_mixer.py,
  run_shell_commands.py}` + matching `tests/` — source for reader-tt4 (lib/voice.py)
  and reader-8f2.5 (pipeline/stitch.py ffmpeg helpers).
- `/Users/Daniel.Michaelis/abogen/abogen/chunking.py` — source for reader-8f2.2
  (done — see closed iteration above).
- `/Users/Daniel.Michaelis/abogen/abogen/{kokoro_text_normalization.py,
  normalization_settings.py,llm_client.py}` — source for reader-8f2.12
  (lib/text_normalization.py, slimmed per reader-8f2.2's scope decision above);
  `apply_phoneme_hints` portion goes to reader-8f2.4 (engines/kokoro.py) instead.
- `/Users/Daniel.Michaelis/abogen/abogen/word_substitution.py` — source for
  reader-8f2.1 (lib/cleaning.py).
