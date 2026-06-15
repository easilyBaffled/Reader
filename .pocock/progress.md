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
