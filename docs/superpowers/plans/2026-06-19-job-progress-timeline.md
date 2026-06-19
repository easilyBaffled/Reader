# Job Progress Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every job a persisted, per-step timeline (DB-backed), with live engine-retry visibility and chunk-rate ETA during the `generating` stage, viewable in a collapsible panel on each Queue tab job card.

**Architecture:** A new `job_events` table receives one row per meaningful pipeline step (reusing the stage-text work already in `core/pipeline.py`). `TTSEngine.synthesize()` gains an `on_retry` callback so `pipeline.py` can count retries/failures live during `generating` and emit throttled progress+ETA events. A new `GET /api/jobs/<id>/events` (JSON) and `GET /web/jobs/<id>/events` (HTML partial) expose the timeline; the Queue tab's existing per-job card gets a "Details" toggle that either appends live SSE updates (active job) or fetches the saved timeline once via HTMX (everyone else).

**Tech Stack:** Python 3.12, Flask, SQLite (numbered `.sql` migrations), HTMX (no JS framework), pytest, httpx `MockTransport` for engine tests.

## Global Constraints

- No raw technical internals (request/response payloads, endpoints) in any logged detail string — spec explicitly scopes this to stage text, timing/ETA, retry/failure visibility only.
- No per-stage ETA outside `generating` — extracting/normalizing/publishing stay text-only, no timing math.
- No per-attempt logging of successful retries — only a running count; only a chunk's **permanent** failure gets its own dedicated event with full error text.
- `job_events` rows are cleaned up via `ON DELETE CASCADE` on `jobs` — no separate pruning code.
- Follow existing test fixture patterns exactly (`app`/`client` fixtures via `create_app(start_worker=False, tts_engine=...)`, `httpx.MockTransport` for engine HTTP, `_insert_job`/`_insert_chunks` helpers) — see each task's Test section for the exact file to copy from.

---

### Task 1: `job_events` table (migration)

**Files:**
- Create: `audibleweb/migrations/006_job_events.sql`
- Modify: `tests/test_db.py:9,23` (version assertions `5` → `6`)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `job_events` table — columns `id` (autoincrement PK), `job_id` (FK → `jobs.id`, `ON DELETE CASCADE`), `stage` (TEXT), `detail` (TEXT), `created_at` (TEXT). Index `idx_job_events_job` on `(job_id, created_at)`. Every later task that writes/reads this table depends on this exact shape.

- [ ] **Step 1: Write the failing test**

Edit `tests/test_db.py` — bump both version assertions and add a new test:

```python
from audibleweb.db import get_connection, migrate


def test_migrate_creates_schema(tmp_path):
    conn = get_connection(tmp_path / "test.db")

    version = migrate(conn)

    assert version == 6
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"jobs", "chunks", "rss_seen_items", "job_events"} <= tables


def test_migrate_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "test.db")

    migrate(conn)
    version = migrate(conn)

    assert version == 6
    # second run must not error re-creating tables
    conn.execute("SELECT * FROM jobs")
    conn.execute("SELECT * FROM chunks")


def test_job_events_cascade_deletes_with_job(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    migrate(conn)
    now = "2026-06-19T00:00:00+00:00"
    conn.execute(
        "INSERT INTO jobs (id, status, input_type, input_value, created_at, updated_at)"
        " VALUES ('job-1', 'queued', 'raw_text', 'hello', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, stage, detail, created_at)"
        " VALUES ('job-1', 'extracting', 'Reading pasted text', ?)",
        (now,),
    )
    conn.commit()

    conn.execute("DELETE FROM jobs WHERE id = 'job-1'")
    conn.commit()

    rows = conn.execute("SELECT * FROM job_events WHERE job_id = 'job-1'").fetchall()
    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: `test_migrate_creates_schema` and `test_migrate_is_idempotent` FAIL with `assert 5 == 6`; `test_job_events_cascade_deletes_with_job` FAILS with `sqlite3.OperationalError: no such table: job_events`.

- [ ] **Step 3: Write the migration**

Create `audibleweb/migrations/006_job_events.sql`:

```sql
-- Persisted per-job step log: one row per meaningful pipeline step (stage
-- text, retry/failure tally, ETA), shown as a collapsible timeline per job
-- card. Cascade-deletes with its job, same as `chunks`.
CREATE TABLE job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_job_events_job ON job_events(job_id, created_at);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Run full suite to check for collateral version-number breaks**

Run: `uv run pytest -q`
Expected: any other test hardcoding `migrate(conn) == 5` (there shouldn't be any besides `test_db.py`, but check the failure list) now passes too. If something else hardcodes version `5`, bump it the same way.

- [ ] **Step 6: Commit**

```bash
git add audibleweb/migrations/006_job_events.sql tests/test_db.py
git commit -m "feat(db): add job_events table for per-job progress timeline"
```

---

### Task 2: `on_retry` hook on `TTSEngine.synthesize`

**Files:**
- Modify: `audibleweb/engines/base.py:28-39`
- Modify: `audibleweb/engines/kokoro.py:70-156`
- Test: `tests/test_kokoro.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `TTSEngine.synthesize(text, voice, speed=1.0, *, check_cancel=None, on_retry: Callable[[int, Exception], None] | None = None) -> bytes`. `on_retry(attempt, exc)` is called synchronously immediately before each retry backoff sleep — `attempt` is the 0-indexed attempt number that just failed, `exc` is the exception. Never called on the final, exhausted failure (that path raises `KokoroEngineError` instead). Task 4 depends on this exact signature and calling contract.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kokoro.py` (after the existing `# --- _generate_with_retry` section, e.g. right after `test_generate_with_retry_raises_after_exhausting_retries`):

```python
def test_on_retry_called_once_per_failed_attempt():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(500)
        return httpx.Response(200, content=SILENCE_WAV)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    calls = []
    result = run(
        engine.synthesize(
            "Hello world", "af_heart", on_retry=lambda attempt, exc: calls.append(attempt)
        )
    )

    assert result == SILENCE_WAV
    assert calls == [0, 1]  # attempts 0 and 1 failed-then-retried; attempt 2 succeeded


def test_on_retry_not_called_on_final_exhaustion():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    calls = []
    with pytest.raises(KokoroEngineError):
        run(
            engine.synthesize(
                "Hello world", "af_heart", on_retry=lambda attempt, exc: calls.append(attempt)
            )
        )

    # 1 initial + 3 retries = 4 attempts total; on_retry fires for the first
    # 3 failures (attempts 0, 1, 2), not for the 4th (final, exhausted) one.
    assert calls == [0, 1, 2]


def test_on_retry_not_called_on_first_try_success(mock_tts_client):
    calls = []
    engine = _engine(mock_tts_client)
    run(engine.synthesize("Hello world", "af_heart", on_retry=lambda a, e: calls.append(a)))
    assert calls == []


def test_on_retry_called_for_both_legs_of_weighted_blend():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/speech"):
            voice = json.loads(request.read())["voice"]
            if voice == "af_bella":
                return httpx.Response(500)
            return httpx.Response(200, content=SILENCE_WAV)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    calls = []
    run(
        engine.synthesize(
            "Hello", "af_heart:0.6+af_bella:0.4",
            on_retry=lambda attempt, exc: calls.append(attempt),
        )
    )

    # af_bella fails every attempt (1 initial + 3 retries -> on_retry fires
    # for attempts 0, 1, 2); af_heart succeeds first try -> no on_retry calls.
    assert calls == [0, 1, 2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_kokoro.py -v -k on_retry`
Expected: all 4 FAIL with `TypeError: synthesize() got an unexpected keyword argument 'on_retry'`.

- [ ] **Step 3: Add `on_retry` to the Protocol**

In `audibleweb/engines/base.py`, replace the `synthesize` method (lines 28-39):

```python
    async def synthesize(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        *,
        check_cancel: Callable[[], Awaitable[None]] | None = None,
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> bytes:
        """Synthesize `text` as `voice` (a voice spec string, see lib/voice.py)
        at `speed`. Returns WAV audio bytes. If `check_cancel` is provided it is
        awaited after synthesis; callers use it for cooperative pause/cancel (D6).
        If `on_retry` is provided, it's called synchronously with
        (attempt_number, exception) immediately before each retry backoff
        sleep -- never on the final, exhausted failure (that path raises
        instead)."""
        ...
```

- [ ] **Step 4: Thread `on_retry` through `KokoroEngine`**

In `audibleweb/engines/kokoro.py`, replace `synthesize`, `_synthesize_weighted`, and `_generate_with_retry` (lines 70-156):

```python
    async def synthesize(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        *,
        check_cancel: Callable[[], Awaitable[None]] | None = None,
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> bytes:
        spec = parse_voice_spec(voice)

        if spec.type == "native":
            result = await self._generate_with_retry(
                text, spec.native_string, speed, on_retry=on_retry
            )
        else:
            result = await self._synthesize_weighted(text, spec, speed, on_retry=on_retry)

        if check_cancel is not None:
            await check_cancel()

        return result

    async def _synthesize_weighted(
        self,
        text: str,
        spec: VoiceSpec,
        speed: float,
        *,
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> bytes:
        """Synthesize a weighted blend; falls back to the surviving voice if one leg fails."""
        voice_a, voice_b = spec.voices
        raw = await asyncio.gather(
            self._generate_with_retry(text, voice_a.name, speed, on_retry=on_retry),
            self._generate_with_retry(text, voice_b.name, speed, on_retry=on_retry),
            return_exceptions=True,
        )
        buf_a: bytes | BaseException = raw[0]
        buf_b: bytes | BaseException = raw[1]

        a_ok = not isinstance(buf_a, Exception)
        b_ok = not isinstance(buf_b, Exception)

        if a_ok and b_ok:
            return mix_weighted_blend(buf_a, voice_a.weight, buf_b, voice_b.weight)  # type: ignore[arg-type]
        if a_ok:
            return buf_a  # type: ignore[return-value]
        if b_ok:
            return buf_b  # type: ignore[return-value]
        cause = buf_a if isinstance(buf_a, Exception) else None
        raise KokoroEngineError(
            f"Both voices in weighted blend failed: {buf_a}; {buf_b}"
        ) from cause

    async def list_voices(self) -> list[str]:
        response = await self._client.get("/audio/voices")
        response.raise_for_status()
        return response.json()["voices"]

    async def _generate_with_retry(
        self,
        text: str,
        voice: str,
        speed: float,
        *,
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> bytes:
        """Generate one voice's audio, retrying with exponential backoff + jitter.

        Total attempts: 1 + MAX_RETRIES. Error discrimination: none -- any
        exception (HTTP error, timeout, connection failure) is retried
        identically, matching audiobook-creator's contract.
        """
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with self._semaphore:
                    response = await self._client.post(
                        "/audio/speech",
                        json={
                            "model": self._model,
                            "voice": voice,
                            "input": text,
                            "response_format": "wav",
                            "speed": speed,
                        },
                    )
                    response.raise_for_status()
                    data = response.content
                    _validate_wav_header(data)
                    return data
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    if on_retry is not None:
                        on_retry(attempt, exc)
                    delay = min(BASE_DELAY_SEC * (2**attempt), MAX_DELAY_SEC)
                    await asyncio.sleep(delay + random.uniform(0, 0.1) * delay)

        raise KokoroEngineError(
            f"TTS request for voice {voice!r} failed after "
            f"{MAX_RETRIES + 1} attempts: {last_error}"
        ) from last_error
```

(Only the four method signatures and the `if on_retry is not None: on_retry(attempt, exc)` line are new — everything else is unchanged from the current file, reproduced here in full because `list_voices` sits between `_synthesize_weighted` and `_generate_with_retry` and must not be dropped.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_kokoro.py -v`
Expected: all tests in the file PASS (the 4 new ones plus all pre-existing ones, unaffected since `on_retry` defaults to `None`).

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -q`
Expected: all tests PASS (the `**_` catch-all in `tests/test_pipeline.py`'s fake engines means the new kwarg can't break them).

- [ ] **Step 7: Commit**

```bash
git add audibleweb/engines/base.py audibleweb/engines/kokoro.py tests/test_kokoro.py
git commit -m "feat(engines): add on_retry hook to TTSEngine.synthesize for live retry visibility"
```

---

### Task 3: `_log_event` — persist every existing stage-text call as a timeline row

**Files:**
- Modify: `audibleweb/core/pipeline.py:55-56,71-81,89-90,98-99,131-132,161-184`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `job_events` table (Task 1).
- Produces: `_log_event(conn, job_id, stage, detail) -> None` — replaces `_set_detail` (same `UPDATE jobs SET stage_detail=...` behavior, PLUS one `INSERT INTO job_events`). Task 4 calls this same function for throttled generating-stage events.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py` (after `test_pipeline_chunks_written_to_db`):

```python
def test_pipeline_logs_job_events_per_stage(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")

    config = AppConfig(publisher=PublisherConfig(type="local"))
    run(
        run_pipeline(
            conn,
            "job-1",
            config=config,
            engine=_FakeEngine(),
            pronunciation={},
            data_dir=tmp_path,
        )
    )

    events = conn.execute(
        "SELECT stage, detail FROM job_events WHERE job_id = ? ORDER BY id", ("job-1",)
    ).fetchall()
    stages_seen = [e["stage"] for e in events]
    assert "extracting" in stages_seen
    assert "normalizing" in stages_seen
    assert "generating" in stages_seen
    assert "publishing" in stages_seen
    # the extracting detail should mention raw_text's fixed message
    extracting_details = [e["detail"] for e in events if e["stage"] == "extracting"]
    assert extracting_details == ["Reading pasted text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline.py::test_pipeline_logs_job_events_per_stage -v`
Expected: FAIL — `events` is an empty list (no rows in `job_events` yet), so `assert "extracting" in stages_seen` fails.

- [ ] **Step 3: Rename `_set_detail` to `_log_event`, write to both columns**

In `audibleweb/core/pipeline.py`, replace the `_set_detail` function (lines 169-174):

```python
def _log_event(conn: sqlite3.Connection, job_id: str, stage: str, detail: str) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE jobs SET stage_detail=?, updated_at=? WHERE id=?",
        (detail, now, job_id),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, stage, detail, created_at) VALUES (?, ?, ?, ?)",
        (job_id, stage, detail, now),
    )
    conn.commit()
```

- [ ] **Step 4: Update every call site to pass the stage explicitly**

In `run_pipeline` (same file), replace each `_set_detail(...)` call:

Line 56 — `_set_detail(conn, job_id, _extracting_detail(row["input_type"], row["input_value"]))` becomes:
```python
    _log_event(
        conn, job_id, "extracting",
        _extracting_detail(row["input_type"], row["input_value"]),
    )
```

Lines 72, 76, 78, 81 (normalizing stage) become:
```python
    _log_event(conn, job_id, "normalizing", "Cleaning text")
    text = clean_text(article.text)
    norm_cfg = config.normalization
    if norm_cfg.llm_enabled and norm_cfg.llm_base_url.strip() and norm_cfg.llm_model.strip():
        _log_event(conn, job_id, "normalizing", f"Normalizing via LLM ({norm_cfg.llm_model})")
    text = await normalize_text(text, norm_cfg)
    _log_event(conn, job_id, "normalizing", "Applying pronunciation overrides")
    text = apply_pronunciation_overrides(text, pronunciation)

    _log_event(conn, job_id, "normalizing", "Splitting into chunks")
```

Line 90 (generating stage) becomes:
```python
    _log_event(conn, job_id, "generating", f"Synthesizing {len(text_chunks)} segments")
```

Line 99 (publishing stage) becomes:
```python
    _log_event(conn, job_id, "publishing", "Stitching audio")
```

Lines 131-132 (the `on_progress` lambda passed to `_build_publisher`) become:
```python
    publisher = _build_publisher(
        config, data_dir,
        on_progress=lambda detail: _log_event(conn, job_id, "publishing", detail),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_pipeline.py::test_pipeline_logs_job_events_per_stage -v`
Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -q`
Expected: all PASS — no other file references `_set_detail` (it was only ever called from within `pipeline.py` and from the `on_progress` lambda, both updated above).

- [ ] **Step 7: Commit**

```bash
git add audibleweb/core/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): persist every stage-detail update as a job_events row"
```

---

### Task 4: Generating-stage retry/failure counters, throttled progress events, permanent-failure events

**Files:**
- Modify: `audibleweb/core/pipeline.py:161-167` (add `_format_duration` near `_set_status`), `209-252` (`_synthesize_all`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `_log_event` (Task 3), `on_retry` kwarg on `TTSEngine.synthesize` (Task 2).
- Produces: no new public function — `_synthesize_all`'s behavior changes (same signature, same return type `list[Path]`), now also emits `job_events` rows during generating. `_format_duration(seconds: float) -> str` is new but private/internal to this module.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py`:

```python
def test_pipeline_logs_permanent_chunk_failure_event(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(
        conn, "job-1",
        input_value="First sentence here. Second sentence fails badly. Third one too.",
    )

    class _OneBadChunkEngine:
        name = "onebad"
        supports_blending = False

        async def synthesize(self, text: str, voice: str, speed: float = 1.0, **_) -> bytes:
            if "fails" in text:
                raise RuntimeError("synthesis exploded")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 24000)
            return buf.getvalue()

    config = AppConfig(publisher=PublisherConfig(type="local"))
    with pytest.raises(RuntimeError, match="synthesis exploded"):
        run(
            run_pipeline(
                conn, "job-1", config=config, engine=_OneBadChunkEngine(),
                pronunciation={}, data_dir=tmp_path,
            )
        )

    events = conn.execute(
        "SELECT detail FROM job_events WHERE job_id = ? AND stage = 'generating'"
        " ORDER BY id",
        ("job-1",),
    ).fetchall()
    failure_events = [e["detail"] for e in events if "failed permanently" in e["detail"]]
    assert len(failure_events) == 1
    assert "synthesis exploded" in failure_events[0]


def test_format_duration_formats_minutes_and_seconds():
    from audibleweb.core.pipeline import _format_duration

    assert _format_duration(5) == "5s"
    assert _format_duration(65) == "1m 5s"
    assert _format_duration(0) == "0s"
    assert _format_duration(-3) == "0s"  # clamp, never show negative


def test_synthesize_all_counts_retries_into_progress_event(tmp_path):
    from audibleweb.core.pipeline import _synthesize_all

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")
    now = "2026-06-19T00:00:00+00:00"
    # 10 chunks -- exactly the throttle floor, guarantees one emitted event.
    for i in range(10):
        conn.execute(
            "INSERT INTO chunks (job_id, chunk_index, text, status, created_at, updated_at)"
            " VALUES ('job-1', ?, ?, 'pending', ?, ?)",
            (i, f"chunk {i}", now, now),
        )
    conn.commit()

    class _RetryOnceEngine:
        name = "retryonce"
        supports_blending = False

        def __init__(self):
            self.calls = 0

        async def synthesize(self, text, voice, speed=1.0, *, on_retry=None, **_):
            self.calls += 1
            if self.calls == 1 and on_retry is not None:
                on_retry(0, RuntimeError("transient"))
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(b"\x00\x00" * 24000)
            return buf.getvalue()

    run(
        _synthesize_all(
            conn, "job-1", [f"chunk {i}" for i in range(10)], _RetryOnceEngine(),
            "af_heart", 1.0, tmp_path,
        )
    )

    events = conn.execute(
        "SELECT detail FROM job_events WHERE job_id = ? AND stage = 'generating'"
        " ORDER BY id",
        ("job-1",),
    ).fetchall()
    progress_events = [e["detail"] for e in events if "/10 segments" in e["detail"]]
    assert len(progress_events) >= 1
    assert "1 retries" in progress_events[0]


def test_synthesize_all_emits_throttled_progress_every_ten_chunks(tmp_path):
    from audibleweb.core.pipeline import _synthesize_all

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    migrate(conn)
    _insert_job(conn, "job-1")
    now = "2026-06-19T00:00:00+00:00"
    chunks = [f"chunk {i}" for i in range(12)]
    for i, text in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks (job_id, chunk_index, text, status, created_at, updated_at)"
            " VALUES ('job-1', ?, ?, 'pending', ?, ?)",
            (i, text, now, now),
        )
    conn.commit()

    run(
        _synthesize_all(
            conn, "job-1", chunks, _FakeEngine(), "af_heart", 1.0, tmp_path,
        )
    )

    events = conn.execute(
        "SELECT detail FROM job_events WHERE job_id = ? AND stage = 'generating'"
        " ORDER BY id",
        ("job-1",),
    ).fetchall()
    # 12 chunks, throttle fires every 10 -> at least one "X/12 segments" event
    progress_events = [e["detail"] for e in events if "/12 segments" in e["detail"]]
    assert len(progress_events) >= 1
    assert "remaining" in progress_events[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v -k "permanent_chunk_failure or format_duration or synthesize_all"`
Expected: `test_pipeline_logs_permanent_chunk_failure_event` FAILS (no `"failed permanently"` text exists yet); `test_format_duration_formats_minutes_and_seconds` FAILS with `ImportError`/`AttributeError` (`_format_duration` doesn't exist); `test_synthesize_all_counts_retries_into_progress_event` and `test_synthesize_all_emits_throttled_progress_every_ten_chunks` both FAIL (no matching `job_events` rows exist yet, since `on_retry` isn't passed into `engine.synthesize()` and no throttled event is ever logged).

- [ ] **Step 3: Add `_format_duration`**

In `audibleweb/core/pipeline.py`, add right after `_set_status` (after line 166, before `_log_event`):

```python
def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
```

- [ ] **Step 4: Add throttling constants near the top of the file**

Right after `logger = logging.getLogger(__name__)` (line 33), add:

```python
PROGRESS_EVERY_CHUNKS = 10
PROGRESS_EVERY_SEC = 15.0
```

- [ ] **Step 5: Rewrite `_synthesize_all` to track counts and emit events**

Replace the whole `_synthesize_all` function (lines 209-252):

```python
async def _synthesize_all(
    conn: sqlite3.Connection,
    job_id: str,
    text_chunks: list[str],
    engine: TTSEngine,
    voice: str,
    speed: float,
    data_dir: Path,
) -> list[Path]:
    chunk_dir = job_audio_dir(data_dir, job_id)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    total = len(text_chunks)
    start = time.monotonic()
    counts = {"resolved": 0, "retries": 0, "failed": 0}
    last_emit = {"at": start, "resolved": 0}

    def _maybe_emit_progress() -> None:
        now = time.monotonic()
        enough_chunks = counts["resolved"] - last_emit["resolved"] >= PROGRESS_EVERY_CHUNKS
        enough_time = now - last_emit["at"] >= PROGRESS_EVERY_SEC
        if not (enough_chunks or enough_time):
            return
        elapsed = now - start
        rate = elapsed / max(counts["resolved"], 1)
        remaining = rate * (total - counts["resolved"])
        _log_event(
            conn, job_id, "generating",
            f"{counts['resolved']}/{total} segments "
            f"({counts['retries']} retries, {counts['failed']} failed) -- "
            f"~{_format_duration(remaining)} remaining",
        )
        last_emit["at"] = now
        last_emit["resolved"] = counts["resolved"]

    async def _synth_one(idx: int, text: str) -> Path:
        wav_path = chunk_dir / f"chunk_{idx:03d}.wav"

        def _on_retry(attempt: int, exc: Exception) -> None:
            counts["retries"] += 1

        try:
            wav = await engine.synthesize(text, voice, speed, on_retry=_on_retry)
            wav_path.write_bytes(wav)
            conn.execute(
                "UPDATE chunks SET status='done', audio_path=?, updated_at=? "
                "WHERE job_id=? AND chunk_index=?",
                (str(wav_path), datetime.now(UTC).isoformat(), job_id, idx),
            )
            conn.commit()
            counts["resolved"] += 1
            _maybe_emit_progress()
            logger.debug("chunk %d/%d synthesized", idx + 1, total)
            return wav_path
        except Exception as exc:
            conn.execute(
                "UPDATE chunks SET status='failed', error=?, updated_at=? "
                "WHERE job_id=? AND chunk_index=?",
                (str(exc), datetime.now(UTC).isoformat(), job_id, idx),
            )
            conn.commit()
            counts["resolved"] += 1
            counts["failed"] += 1
            _log_event(
                conn, job_id, "generating", f"chunk {idx} failed permanently: {exc}"
            )
            raise

    results = await asyncio.gather(
        *[_synth_one(i, t) for i, t in enumerate(text_chunks)],
        return_exceptions=True,
    )

    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        raise errors[0]

    return list(results)  # type: ignore[return-value]
```

- [ ] **Step 6: Add the `time` import**

At the top of `audibleweb/core/pipeline.py`, the import block currently has `import sqlite3` then `from collections.abc import Callable` — add `import time` alphabetically among the stdlib imports:

```python
import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: all PASS, including `test_pipeline_logs_permanent_chunk_failure_event`, `test_format_duration_formats_minutes_and_seconds`, `test_synthesize_all_counts_retries_into_progress_event`, `test_synthesize_all_emits_throttled_progress_every_ten_chunks`.

- [ ] **Step 8: Run full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add audibleweb/core/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): throttled progress/ETA + permanent-failure events during generating"
```

---

### Task 5: `GET /api/jobs/<job_id>/events`

**Files:**
- Modify: `audibleweb/api/routes.py:196-216` (insert new route after `download_job_audio`, before `delete_job`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `job_events` table (Task 1).
- Produces: `GET /api/jobs/<job_id>/events` → `404 {"error": "job not found"}` for unknown job, else `200 {"events": [{"stage": str, "detail": str, "created_at": str}, ...]}` ordered oldest-first.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api.py` (after the existing audio-download tests, before `# --- POST /api/jobs/:id/retry`):

```python
# --- GET /api/jobs/:id/events ----------------------------------------------------


def test_list_job_events_not_found(client):
    resp = client.get("/api/jobs/missing/events")
    assert resp.status_code == 404


def test_list_job_events_empty_for_job_with_no_events(app, client):
    _insert_job(app, "job-1")
    resp = client.get("/api/jobs/job-1/events")
    assert resp.status_code == 200
    assert resp.get_json() == {"events": []}


def test_list_job_events_returns_ordered_list(app, client):
    _insert_job(app, "job-1")
    conn = get_connection(app.config["DB_PATH"])
    conn.execute(
        "INSERT INTO job_events (job_id, stage, detail, created_at) VALUES"
        " ('job-1', 'extracting', 'Reading pasted text', '2026-06-19T00:00:01+00:00'),"
        " ('job-1', 'normalizing', 'Cleaning text', '2026-06-19T00:00:02+00:00')"
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/jobs/job-1/events")

    assert resp.status_code == 200
    events = resp.get_json()["events"]
    assert len(events) == 2
    assert events[0]["stage"] == "extracting"
    assert events[0]["detail"] == "Reading pasted text"
    assert events[1]["stage"] == "normalizing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v -k list_job_events`
Expected: all 3 FAIL with 404 (route doesn't exist yet — Flask returns its own 404 for an unregistered path, but check the body isn't `{"error": "job not found"}` — actually for `test_list_job_events_not_found` this could accidentally pass since both real-404 and not-found-404 are status 404. The other two will clearly FAIL since `resp.status_code` is 404, not 200.

- [ ] **Step 3: Add the route**

In `audibleweb/api/routes.py`, insert after `download_job_audio` ends (line 215) and before `@api_bp.delete("/jobs/<job_id>")` (line 218):

```python
@api_bp.get("/jobs/<job_id>/events")
def list_job_events(job_id: str):
    conn = _db()
    try:
        row = _fetch_job(conn, job_id)
        if row is None:
            return jsonify({"error": "job not found"}), 404
        events = conn.execute(
            "SELECT stage, detail, created_at FROM job_events"
            " WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
    finally:
        conn.close()

    return jsonify({"events": [dict(e) for e in events]})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v -k list_job_events`
Expected: all 3 PASS.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add audibleweb/api/routes.py tests/test_api.py
git commit -m "feat(api): add GET /api/jobs/:id/events for the persisted progress timeline"
```

---

### Task 6: Static timeline panel (HTMX) for non-active job cards

**Files:**
- Modify: `audibleweb/web/templates/macros.html:18-21` (add `chevron-down` icon next to `upload`)
- Modify: `audibleweb/web/routes.py:208-210` (new route)
- Create: `audibleweb/web/templates/partials/job_events.html`
- Modify: `audibleweb/web/templates/partials/queue.html:134-147` (compact-row actions get the toggle)
- Modify: `audibleweb/static/css/app.css` (append after `.job-actions`, ~line 318)
- Test: `tests/test_web_ui.py`

**Interfaces:**
- Consumes: `job_events` table (Task 1).
- Produces: `GET /web/jobs/<job_id>/events` → renders `partials/job_events.html` (HTML fragment, a `<ul class="job-timeline">`). Task 7 reuses the same `<ul id="job-timeline-{{ job.id }}">` element id convention for the active-job card.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`, inside `class TestQueueTab` (after `test_queue_shows_status_badge`):

```python
    def test_queue_compact_row_has_details_toggle(self, app, client):
        _insert_job(app, "job-fail", status="failed", title="Failed Job")
        resp = client.get("/tab/queue")
        html = resp.data.decode()
        assert "job-details-toggle" in html
        assert '/web/jobs/job-fail/events' in html

    def test_job_events_endpoint_renders_timeline(self, app, client):
        from audibleweb.db import get_connection

        _insert_job(app, "job-1", status="done", title="Done Job")
        conn = get_connection(app.config["DB_PATH"])
        conn.execute(
            "INSERT INTO job_events (job_id, stage, detail, created_at) VALUES"
            " ('job-1', 'extracting', 'Reading pasted text', '2026-06-19T00:00:01+00:00')"
        )
        conn.commit()
        conn.close()

        resp = client.get("/web/jobs/job-1/events")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "job-timeline" in html
        assert "Reading pasted text" in html

    def test_job_events_endpoint_empty_timeline(self, app, client):
        _insert_job(app, "job-1", status="queued", title="Queued Job")
        resp = client.get("/web/jobs/job-1/events")
        assert resp.status_code == 200
        assert "No timeline yet" in resp.data.decode()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_ui.py -v -k "details_toggle or job_events_endpoint"`
Expected: all 3 FAIL — `test_queue_compact_row_has_details_toggle` fails on the `job-details-toggle` assertion (class doesn't exist yet); the other two FAIL with 404 (route doesn't exist).

- [ ] **Step 3: Add the `chevron-down` icon**

In `audibleweb/web/templates/macros.html`, add a new branch right after the `'upload'` branch (after line 21, before `{%- elif name == 'download' %}`):

```jinja
{%- elif name == 'chevron-down' %}
  <polyline points="6 9 12 15 18 9"/>
```

- [ ] **Step 4: Create the partial template**

Create `audibleweb/web/templates/partials/job_events.html`:

```html
<ul class="job-timeline">
  {% for event in events %}
    <li><span class="job-timeline-stage">{{ event.stage }}</span> {{ event.detail }}</li>
  {% else %}
    <li class="job-timeline-empty">No timeline yet.</li>
  {% endfor %}
</ul>
```

- [ ] **Step 5: Add the web route**

In `audibleweb/web/routes.py`, insert between line 208 (`return render_template(...)`'s closing `)`) and the blank lines before `_coerce_settings_field` (line 211):

```python
@web_bp.get("/web/jobs/<job_id>/events")
def job_events(job_id: str):
    conn = _db()
    try:
        events = conn.execute(
            "SELECT stage, detail, created_at FROM job_events"
            " WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
    finally:
        conn.close()
    return render_template("partials/job_events.html", events=events)
```

- [ ] **Step 6: Add the toggle button to compact job-card rows**

In `audibleweb/web/templates/partials/queue.html`, the compact-row `job-actions` div (lines 134-147) currently starts with:

```html
            <div class="job-actions">
              {{ status_badge(job.status) }}
              {% if job.status == 'done' and job.public_url %}
```

Change it to insert the toggle right after `status_badge`:

```html
            <div class="job-actions">
              {{ status_badge(job.status) }}
              <button class="btn-icon job-details-toggle"
                      aria-label="Show details" aria-expanded="false"
                      hx-get="/web/jobs/{{ job.id }}/events"
                      hx-target="#job-timeline-{{ job.id }}"
                      hx-trigger="click once"
                      onclick="toggleJobDetails('{{ job.id }}')">
                {{ icon('chevron-down', 16) }}
              </button>
              {% if job.status == 'done' and job.public_url %}
```

Then, immediately after the compact row's closing `</div>` for `job-card-header` (right before the article's closing `</article>` tag, i.e. right after line 176's `</div>` and before line 177's `</article>`), add the empty timeline container:

```html
          </div>
          <ul class="job-timeline" id="job-timeline-{{ job.id }}" hidden></ul>
        </article>
```

- [ ] **Step 7: Define `toggleJobDetails` unconditionally**

In the same file, the job-list `<div>` is only preceded by the `{% else %}` of `{% if jobs is not defined or jobs | length == 0 %}`. Add a script block right after that `{% else %}` line (before `<div class="job-list" id="job-list">`):

```html
{% else %}
  <script>
    function toggleJobDetails(jobId) {
      var ul = document.getElementById("job-timeline-" + jobId);
      if (ul) ul.hidden = !ul.hidden;
    }
  </script>
  <div class="job-list" id="job-list">
```

- [ ] **Step 8: Add minimal CSS**

In `audibleweb/static/css/app.css`, right after the existing `.job-actions { ... }` block, add:

```css
.job-timeline {
  margin: var(--space-sm) 0 0;
  padding-left: var(--space-lg);
  font-size: 13px;
  color: var(--text-muted);
  max-height: 200px;
  overflow-y: auto;
}

.job-timeline li {
  padding: 2px 0;
}

.job-timeline-stage {
  font-weight: 500;
  color: var(--text);
}

.job-timeline-empty {
  list-style: none;
  padding-left: calc(var(--space-lg) * -1);
}
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_ui.py -v -k "details_toggle or job_events_endpoint"`
Expected: all 3 PASS.

- [ ] **Step 10: Run full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 11: Manual check (no automated CSS/JS test exists in this repo)**

Run: `uv run audibleweb` (or rely on the already-running dev server), open `http://127.0.0.1:5000/tab/queue` in a browser, click the chevron on any non-active job card. Expected: panel expands, shows "No timeline yet." for an old job (no `job_events` rows) or the saved steps for a job run after this change.

- [ ] **Step 12: Commit**

```bash
git add audibleweb/web/templates/macros.html audibleweb/web/templates/partials/job_events.html audibleweb/web/templates/partials/queue.html audibleweb/web/routes.py audibleweb/static/css/app.css tests/test_web_ui.py
git commit -m "feat(web): collapsible per-job timeline panel for non-active job cards"
```

---

### Task 7: Live timeline append for the active job card

**Files:**
- Modify: `audibleweb/web/templates/partials/queue.html:45-106` (active job card markup + inline `<script>`)
- Test: `tests/test_web_ui.py` (markup-only assertion; live SSE behavior is manually verified — no JS test harness exists in this repo)

**Interfaces:**
- Consumes: `stage_detail` field already present in the SSE payload (`api/sse.py:42`, done in an earlier session — no backend change needed in this task). Same `#job-timeline-{{ job.id }}` element id convention as Task 6.
- Produces: nothing new for later tasks — this is the last task in the plan.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_web_ui.py`, inside `class TestQueueTab`:

```python
    def test_queue_active_job_has_details_toggle_and_timeline_container(self, app, client):
        _insert_job(app, "job-gen", status="generating", title="Active Job")
        resp = client.get("/tab/queue")
        html = resp.data.decode()
        assert "job-details-toggle" in html
        assert 'id="job-timeline-job-gen"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_web_ui.py -v -k active_job_has_details_toggle`
Expected: FAIL — the active job card (unlike the compact rows touched in Task 6) has no toggle button or timeline container yet.

- [ ] **Step 3: Add the toggle + timeline container to the active card**

In `audibleweb/web/templates/partials/queue.html`, the active card's header (lines 45-58) currently is:

```html
      <article class="job-card job-card-active" id="job-{{ job.id }}">
        <div class="job-card-header">
          <div class="job-title">{{ job.title or job.input_value }}</div>
          <div class="job-actions">
            {{ status_badge(job.status) }}
            <button class="btn-icon"
                    hx-post="/api/jobs/{{ job.id }}/pause"
                    hx-target="#job-{{ job.id }}"
                    hx-swap="outerHTML"
                    aria-label="Pause job">
              {{ icon('pause') }}
            </button>
          </div>
        </div>
```

Change the `job-actions` div to add the toggle (note: no `hx-get` here, unlike the compact-row version — the active job's timeline is filled live by the SSE handler in Step 4, not fetched):

```html
      <article class="job-card job-card-active" id="job-{{ job.id }}">
        <div class="job-card-header">
          <div class="job-title">{{ job.title or job.input_value }}</div>
          <div class="job-actions">
            {{ status_badge(job.status) }}
            <button class="btn-icon job-details-toggle"
                    aria-label="Show details" aria-expanded="false"
                    onclick="toggleJobDetails('{{ job.id }}')">
              {{ icon('chevron-down', 16) }}
            </button>
            <button class="btn-icon"
                    hx-post="/api/jobs/{{ job.id }}/pause"
                    hx-target="#job-{{ job.id }}"
                    hx-swap="outerHTML"
                    aria-label="Pause job">
              {{ icon('pause') }}
            </button>
          </div>
        </div>
```

Then, right after the `progress-wrap` div closes (after line 73's `</div>`, before line 74's `</article>`), add:

```html
        <ul class="job-timeline" id="job-timeline-{{ job.id }}" hidden></ul>
      </article>
```

- [ ] **Step 4: Append live timeline entries from the SSE handler**

In the same file's inline `<script>` block (lines 75-106), the `es.onmessage` handler currently is:

```javascript
          es.onmessage = function (e) {
            var d = JSON.parse(e.data);
            var fill = document.getElementById("progress-fill-" + jobId);
            var label = document.getElementById("progress-label-" + jobId);
            if (!fill || !label) { es.close(); return; }
            var pct = stageProgress[d.status] || 0;
            var labelText = d.stage_detail || stageLabels[d.status] || d.status;
            if (d.status === "generating" && d.chunks_total > 0) {
              pct = 30 + Math.round((d.chunks_done / d.chunks_total) * 60);
              labelText = "Synthesizing audio: segment " + d.chunks_done + "/" + d.chunks_total;
            }
            fill.style.width = pct + "%";
            label.textContent = labelText;
            if (d.status === "done" || d.status === "failed") {
              es.close();
              htmx.ajax("GET", "/tab/queue", { target: "#main-content", swap: "innerHTML" });
            }
          };
```

Add a `lastDetail` tracking variable right before `es.onmessage = function (e) {` and an append block inside it:

```javascript
          var lastDetail = null;
          es.onmessage = function (e) {
            var d = JSON.parse(e.data);
            var fill = document.getElementById("progress-fill-" + jobId);
            var label = document.getElementById("progress-label-" + jobId);
            if (!fill || !label) { es.close(); return; }
            var pct = stageProgress[d.status] || 0;
            var labelText = d.stage_detail || stageLabels[d.status] || d.status;
            if (d.status === "generating" && d.chunks_total > 0) {
              pct = 30 + Math.round((d.chunks_done / d.chunks_total) * 60);
              labelText = "Synthesizing audio: segment " + d.chunks_done + "/" + d.chunks_total;
            }
            fill.style.width = pct + "%";
            label.textContent = labelText;
            if (d.stage_detail && d.stage_detail !== lastDetail) {
              lastDetail = d.stage_detail;
              var timeline = document.getElementById("job-timeline-" + jobId);
              if (timeline) {
                var li = document.createElement("li");
                li.innerHTML = '<span class="job-timeline-stage">' + d.status + '</span> ' + d.stage_detail;
                timeline.appendChild(li);
              }
            }
            if (d.status === "done" || d.status === "failed") {
              es.close();
              htmx.ajax("GET", "/tab/queue", { target: "#main-content", swap: "innerHTML" });
            }
          };
```

Note `d.status` and `d.stage_detail` come from the SSE payload, both already strings controlled by this codebase (not user input), so `innerHTML` here carries no injection risk beyond what already exists in this trusted data path.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_web_ui.py -v -k active_job_has_details_toggle`
Expected: PASS.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 7: Manual verification (no JS test harness in this repo)**

With the dev server running, submit a job large enough to sit in `generating` for a while (or any job), open the Queue tab, click the chevron on the active job card before it finishes. Expected: panel expands empty (or with whatever's accumulated so far), then a new `<li>` appears each time the stage text changes (e.g. "Cleaning text" → "Applying pronunciation overrides" → "Splitting into chunks" → progress lines like "10/50 segments (0 retries, 0 failed) -- ~45s remaining"). Confirm the line count grows roughly in line with what's in `job_events` for that job (`sqlite3 data/audibleweb.db "SELECT * FROM job_events WHERE job_id='<id>'"`).

- [ ] **Step 8: Commit**

```bash
git add audibleweb/web/templates/partials/queue.html tests/test_web_ui.py
git commit -m "feat(web): live-append progress timeline on the active job card via SSE"
```

---

## Final check

- [ ] Run `uv run pytest -q` once more from a clean tree — full suite green.
- [ ] Run `uv run ruff check .` — no lint errors.
- [ ] Run `uv run ruff format --check .` — no formatting diffs (run `uv run ruff format .` if it complains, then re-run tests).
