# Archive

Older iteration entries moved out of progress.md's rolling window.

---

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
