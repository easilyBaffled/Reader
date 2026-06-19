# Job progress timeline — design

## Context

Tonight's earlier work (uncommitted, same session) added per-stage text and
chunk counts to the Queue tab's progress display:

- `audibleweb/core/pipeline.py` calls `_set_detail(conn, job_id, detail)`
  (`pipeline.py:169`) at each stage transition, writing a one-line
  human-readable string (e.g. `"Cloning gh-pages branch..."`,
  `"Cleaning text"`) into `jobs.stage_detail` (migration
  `005_stage_detail.sql`).
- `api/sse.py:_progress()` (`sse.py:19`) streams `stage_detail` plus
  `chunks_done`/`chunks_total` (computed live from the `chunks` table when
  `status == "generating"`) to the active job's progress bar.
- `web/routes.py:_load_jobs()` attaches the same chunk counts to any
  `stalled` job whose `stalled_stage == "generating"`, so a dead job's card
  shows how far it got.
- `publishers/github_pages.py` reports its own git steps ("Cloning...",
  "Committing changes", "Pushing to...") through an `on_progress` callback
  threaded in from `_build_publisher` (`pipeline.py:270`).

This is all *current-state-only*: one column gets overwritten each time.
Once a stage moves on, what happened in the previous stage is gone. There's
no record of how long anything took, no visibility into the engine retries
happening inside `KokoroEngine._generate_with_retry`
(`engines/kokoro.py:122`), and nothing survives a job finishing or failing —
exactly the kind of detail that would have made tonight's Kokoro-instability
debugging (chunk failures, container restarts) immediately visible instead
of requiring a `docker logs` dive.

## Goal

A per-job, persisted timeline of what actually happened, viewable both live
(while a job runs) and after the fact (on a finished, failed, or stalled
job) — without cluttering the Queue tab's normal at-a-glance list.

## Non-goals

- No full debug-console detail (request/response payloads, exact API
  endpoints, voice/engine internals). Scope is: stage text, timing/ETA,
  retry/failure visibility — confirmed with the user, "raw technical
  internals" explicitly declined.
- No per-stage ETA for extracting/normalizing/publishing. Those stages run
  in seconds; only `generating` (minutes, highly variable) gets a
  rate-based ETA.
- No logging of every successful retry attempt. Confirmed with the user:
  summarized counts during the run, full detail only for chunks that
  ultimately fail permanently.
- No retention/pruning policy beyond the existing cascade-delete-on-job
  pattern (`chunks` already works this way).
- No changes to the existing `jobs.stage_detail` column or the SSE/`_load_jobs`
  wiring that already reads it — the timeline is additive, not a replacement.

## Approach

### Data model: `job_events` table

```sql
CREATE TABLE job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_job_events_job ON job_events(job_id, created_at);
```

New migration `006_job_events.sql`. `ON DELETE CASCADE` means
`DELETE FROM jobs WHERE id = ?` (already used by `delete_job`,
`api/routes.py`) cleans up a job's timeline automatically — same pattern
`chunks` already relies on, no new cleanup code needed.

### `_set_detail` becomes `_log_event`: same call sites, one more write

`pipeline.py:169`'s `_set_detail` is renamed `_log_event` and does two
writes instead of one: the existing `UPDATE jobs SET stage_detail=...`
(kept — SSE and `_load_jobs` still read it for the "current" line) plus
`INSERT INTO job_events (job_id, stage, detail, created_at) VALUES (...)`.
Every existing call site (`pipeline.py:56,72,76,78,81,90,99`, plus the
`on_progress` lambda at `pipeline.py:132` feeding `GitHubPagesPublisher`'s
git-step messages) now produces a timeline row with zero call-site changes
beyond the rename — the extracting/normalizing/publishing stages get a
useful timeline "for free" from work already done tonight.

### Engine retry visibility: `on_retry` hook on `TTSEngine.synthesize`

`engines/base.py:28`'s `synthesize()` already has one optional
keyword-only extension point (`check_cancel`, `base.py:34`) — same pattern,
one more:

```python
async def synthesize(
    self, text: str, voice: str, speed: float = 1.0, *,
    check_cancel: Callable[[], Awaitable[None]] | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> bytes: ...
```

`KokoroEngine._generate_with_retry` (`engines/kokoro.py:122`) calls
`on_retry(attempt, exc)` right before each backoff `asyncio.sleep` — i.e. on
every failed-but-retrying attempt, never on the final exhaustion (that path
already raises `KokoroEngineError`, handled at the chunk level). Any future
second engine implementation simply doesn't accept/use the kwarg; Protocol
stays backward compatible.

### Generating stage: throttled progress events + permanent-failure events

`_synthesize_all` (`pipeline.py:209`) gains three closed-over mutables:
`start_time`, `retry_count`, `failed_count`, plus `last_emit_at`/
`last_emit_chunks`. `_synth_one` (`pipeline.py:221`) passes
`on_retry=lambda attempt, exc: retry_count.increment()` into each
`engine.synthesize()` call.

After each chunk resolves (success or permanent failure), check: has it
been ≥10 chunks or ≥15s since the last emitted event? If so:

```python
elapsed = now - start_time
rate = elapsed / max(chunks_done, 1)
remaining = rate * (chunks_total - chunks_done)
_log_event(conn, job_id, "generating",
    f"{chunks_done}/{chunks_total} segments "
    f"({retry_count} retries, {failed_count} failed) -- "
    f"~{format_duration(remaining)} remaining")
```

On a chunk's permanent failure (the `except Exception` branch in
`_synth_one`), log a dedicated event immediately (not throttled):
`_log_event(conn, job_id, "generating", f"chunk {idx} failed permanently: {exc}")`
— the real error text, same string already written to `chunks.error`.

`format_duration` is a small new private helper in `pipeline.py`
(`"4m 12s"` style) — no existing duration formatter to reuse (`queue.html`
does its m/s split inline in Jinja, not from Python), and it's only needed
here, so it doesn't warrant a new `lib/` module.

### API: one new read endpoint, one new SSE field

- `GET /api/jobs/<job_id>/events` (`api/routes.py`, near the other
  `/jobs/<job_id>/...` routes) — `404` if job not found, otherwise
  `{"events": [{"stage", "detail", "created_at"}, ...]}` ordered by
  `created_at`. One-shot fetch for jobs that are no longer active; no
  pagination (a single job's event count is bounded by the throttling
  above — tens, not thousands, of rows even for a 288-chunk run).
- `api/sse.py:_progress()` adds `"stage_detail": job["stage_detail"]`
  to its existing return dict (it already reads the full row into `job`,
  this is one more key) — the active job's panel appends a new line each
  time this value changes, reusing the `EventSource` connection that's
  already open. No second stream.

### UI: collapsible per-job timeline panel

Each job card (`queue.html:48` active card, `queue.html:134` compact rows)
gets a small "Details ▾" toggle button next to the existing status
badge/actions. Collapsed by default.

Expanding renders a `<ul class="job-timeline">`. Two paths depending on
whether the job is the live one:

- **Active job**: the existing inline `<script>` block
  (`queue.html`'s SSE handler) appends an `<li>` each time `d.stage_detail`
  changes, instead of only overwriting the single progress label. No new
  network call.
- **Everyone else** (done/failed/stalled/queued): `hx-get="/api/jobs/{{ job.id }}/events" hx-trigger="click once" hx-swap="innerHTML"`
  on the toggle, swapping in a new small partial
  (`partials/job_events.html`) that renders the `<li>` rows server-side.
  Fits the existing HTMX-first convention with no new JS.

Each `<li>` shows `stage — detail` plus a relative timestamp
(`Xs ago` / `created_at` formatted client-side or server-side — implementer's
choice, doesn't affect the design).

## Testing

- `tests/test_db.py` — migration version bumps to 6; `job_events` table
  exists after `migrate()`.
- `tests/test_pipeline.py` — each stage's `_log_event` call produces a
  `job_events` row with the right `stage`/`detail`; a permanently-failing
  chunk logs its error text; throttled progress events fire at the
  10-chunks-or-15s boundary (fake clock, no real `asyncio.sleep`); ETA math
  is correct for a few known `(elapsed, chunks_done, chunks_total)` inputs.
- `tests/test_kokoro.py` — `on_retry` fires once per failed attempt with
  the right attempt number, never on final exhaustion, never on first-try
  success.
- `tests/test_api.py` — `GET /api/jobs/<id>/events`: 404 unknown job, `[]`
  for a job with no events yet, correctly ordered list otherwise.
- `tests/test_web_ui.py` — Details toggle renders on both active and
  compact job cards; non-active rows carry the expected `hx-get` wiring.
- No live network/TTS calls — same mocked-engine pattern existing pipeline
  tests already use.
