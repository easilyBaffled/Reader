# AudibleWeb — Design Specification

## Overview

AudibleWeb is a local web application that turns URLs, files, and text into podcast episodes published to a personal RSS feed. It combines the best patterns from audiobook-creator (voice blending, async parallel TTS, text processing) and abogen (memory-efficient streaming, entity pronunciation, production packaging) into a new platform-architecture tool.

**One-line:** Multi-format inbox → text pipeline → TTS → podcast feed in your app.

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Input model | Multi-format inbox (URL, PDF, TXT, MD, pasted text, RSS import) | Widest usability. Every input = one episode. No chapter splitting. |
| Runtime | Local web UI (Flask) | User's machine, no external hosting for the app itself |
| Feed publishing | GitHub Pages | Free, stable URL, always-on, no tunnel needed |
| TTS engine | Kokoro default + pluggable (OpenAI-compatible API) | Proven quality, drop-in replacement possible |
| Article extraction | Trafilatura local + Jina Reader API fallback | Robust local-first, handles JS/SPAs via fallback |
| Text normalization | LLM-based (optional) | Abbreviations, numbers, acronyms common in articles |
| Voice features | Blending + speed control, single voice per episode | Articles don't have characters; blending tunes narrator personality |
| Codebase | New project (clean start) | Cherry-pick best patterns, no legacy constraints |
| Architecture | Full platform with plugin system | Extensible extractors, engines, publishers |

---

## 1. Core Architecture

```
+-----------------------------------------------------+
|                   Web UI (Flask + HTMX)              |
|  Inbox - Queue - Feed Manager - Voice Config - Logs |
+------------------------+----------------------------+
                         | REST API
+------------------------v----------------------------+
|                    Core Engine                       |
|                                                     |
|  +----------+  +------------+  +-----------------+ |
|  |Extractors|  | Text       |  | TTS Pipeline    | |
|  |(plugin)  |  | Pipeline   |  | (plugin engine) | |
|  +-----+----+  +-----+------+  +-----+-----------+ |
|        |              |               |             |
|  URL/PDF/TXT   Clean->Norm->    Voice blend/parse  |
|  /RSS/Text     Pronounce        Async parallel gen |
|                                 Speed control      |
+------------------------+----------------------------+
                         |
+------------------------v----------------------------+
|              Job Queue (SQLite)                      |
|  Persistent - Status tracking - Retry - History     |
+------------------------+----------------------------+
                         |
+------------------------v----------------------------+
|              Publishers (plugin)                     |
|  GitHub Pages - Local - (S3/Audiobookshelf future)  |
+-----------------------------------------------------+
```

**Principles:**
- Plugin interfaces for extractors, TTS engines, publishers (Python Protocols)
- REST API is source of truth — web UI and external triggers all hit same endpoints
- SQLite job queue — no Redis/Celery, survives restarts, queryable history
- Job lifecycle: `queued -> extracting -> normalizing -> generating -> publishing -> done | failed`

### 1.1 Concurrency Model (D13)

Flask stays **fully synchronous**. Routes only read and write the `jobs`/`chunks` SQLite tables — they never `await` anything.

All async I/O (TTS calls, HTTP fetches, git push) runs in a **background worker thread** (`audibleweb/worker.py`) that owns its own `asyncio` event loop. The worker polls `jobs` for `status='queued'` entries at a 1-second interval, picks one up, and drives it through the pipeline. Chunk-level parallelism (concurrent TTS API calls) happens inside that event loop via a semaphore-bounded `asyncio.gather`.

```
Flask request thread          Worker thread (daemon)
──────────────────            ────────────────────────────────────────
POST /api/jobs                asyncio.run(Worker._main())
  INSERT jobs (queued)          loop: poll jobs WHERE status='queued'
  return 201                      UPDATE jobs SET status='extracting'
                                  await run_pipeline(job_id)
GET /api/jobs/:id               UPDATE jobs SET status='done'
  SELECT jobs                   sleep(poll_interval)
  return status
```

**Why this shape:**
- Flask's WSGI server handles concurrent HTTP requests in threads without needing an async framework.
- Pipeline stages (TTS, normalization, publishing) are inherently I/O-bound and benefit from `asyncio` concurrency within a job, but there's no benefit to async between jobs (one job at a time is intentional).
- Thread boundary is crossed only via SQLite reads/writes — no shared in-process state, no queues, no locks beyond SQLite's own serialization.

See `audibleweb/worker.py` for the implementation (`Worker.start()`, `Worker._main()`, `_run_with_heartbeat()`).

---

## 2. Plugin System

### 2.1 Extractors

```python
class Extractor(Protocol):
    name: str
    supported_inputs: list[str]  # ["url", "file:pdf", "file:txt", "file:md", "rss", "text"]

    def can_handle(self, input: str) -> bool: ...
    async def extract(self, input: str) -> Article: ...

@dataclass
class Article:
    title: str
    text: str              # cleaned plaintext
    source_url: str | None
    author: str | None
    published: datetime | None
    word_count: int
```

**Built-in extractors:**
- `WebExtractor` — trafilatura -> Jina fallback
- `FileExtractor` — PDF (PyMuPDF), TXT, Markdown
- `RSSImportExtractor` — pull unread items from subscribed RSS feeds
- `RawTextExtractor` — paste/POST raw text directly

### 2.2 TTS Engines

```python
class TTSEngine(Protocol):
    name: str
    supports_blending: bool

    async def synthesize(self, text: str, voice: VoiceConfig, speed: float) -> AudioSegment: ...
    async def list_voices(self) -> list[Voice]: ...
```

**Built-in:** `KokoroEngine` (OpenAI-compatible API). Others drop in by implementing same protocol.

### 2.3 Publishers

```python
class Publisher(Protocol):
    name: str

    async def publish(self, episode: Episode, audio_path: Path) -> str: ...  # returns public URL
    async def update_feed(self, episodes: list[Episode]) -> str: ...  # returns feed URL
```

**Built-in:**
- `GitHubPagesPublisher` — git push MP3 + regenerate feed.xml to gh-pages branch
- `LocalPublisher` — serve from local directory (dev/testing)

### 2.4 Plugin Discovery

Drop a `.py` file in `plugins/{extractors,engines,publishers}/`. App scans on startup, registers anything implementing the protocol. No entry points or setuptools magic.

---

## 3. Text Processing Pipeline

Three stages, each optional/configurable:

### Stage 1: Cleaning (always-on, rule-based)
- Strip residual HTML/Markdown syntax
- Remove boilerplate patterns
- Unicode normalize (smart quotes -> ASCII, em/en dashes -> hyphen, ellipsis -> three dots)
- Emoji removal
- Collapse whitespace

### Stage 2: LLM Normalization (optional, needs LLM API)
- Numbers -> words ("42" -> "forty-two")
- Abbreviations -> expanded ("Dr." -> "Doctor")
- Acronyms -> pronunciation ("API" -> "A-P-I")
- URLs -> "link to [domain]"
- Code blocks -> brief summary or skip
- Chunked to fit LLM context window
- Prompt: "Rewrite for spoken narration. Expand abbreviations, spell out numbers, don't change meaning."
- Graceful degradation: if LLM unavailable or returns garbage, skip this stage and continue

### Stage 3: Pronunciation Overrides (persistent JSON)
- Entity DB lookup (`pronunciation.json`)
- User-editable via web UI
- Applied as find-replace before TTS
- Example: "Kubernetes" -> "Koo-ber-net-eez"

### Chunking for TTS
After normalization, text gets sentence-split (spaCy or rule-based fallback) then batched into chunks respecting TTS engine token window (default: 500 tokens / ~2000 chars for Kokoro). No mid-sentence splits.

---

## 4. TTS & Audio Generation

### Voice Resolution
- Single voice: `"af_heart"`
- Native blend (TTS engine mixes): `"af_heart+af_bella"` (max 3 voices)
- Weighted blend (post-process mix): `"af_heart:0.7+af_bella:0.3"` (max 2, weights sum to 1.0)
- Speed: 0.5-2.0, default 1.0

### Async Parallel Generation
- Semaphore-bounded concurrency (configurable, default 4)
- Each chunk -> TTS API call
- Weighted blend: generate both voices separately, mix audio post-hoc
- Retry with exponential backoff (3 retries, 0.1-10s + jitter)
- Stream to temp files (memory-efficient)

### Stitching
- FFmpeg concat (not PyDub — memory efficient for long articles)
- No silence between chunks
- 0.5s silence at start/end
- Output: single MP3 (CBR 128kbps, ~1MB/min)
- Duration calculated from output file

### Failure Handling
- Individual chunk failure -> retry 3x -> if still fails, mark job failed with chunk index
- TTS server unreachable -> fail fast after health check, don't queue chunks
- Partial output never published — all-or-nothing per episode

---

## 5. Job Queue & Lifecycle

### Schema

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    input_type TEXT NOT NULL,
    input_value TEXT NOT NULL,
    title TEXT,
    source_url TEXT,
    word_count INTEGER,
    audio_duration_sec REAL,
    audio_path TEXT,
    public_url TEXT,
    error TEXT,
    voice_config TEXT,  -- JSON
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### Status Flow

```
queued -> extracting -> normalizing -> generating -> publishing -> done
                                                                    |
         (any stage can fail)                                       |
         failed <--------------------------------------------------+
```

### Behavior
- Jobs persist across restarts
- On startup: intermediate-state jobs reset to `queued` for retry
- Failed jobs stay in DB with error. User can retry from UI.
- Queue processes one job at a time (parallelism is within-job at chunk level)
- Partial state preserved: if extraction succeeded, retry skips extraction

### Triggers

| Trigger | Method |
|---------|--------|
| Web UI | Form submit in inbox |
| REST API | `POST /api/jobs` with `{input, type, voice_config?}` |
| Bookmarklet | Hits REST API with current page URL |
| iOS Shortcut | Share sheet -> HTTP POST to API |
| CLI | `curl` or thin wrapper script |
| RSS watcher | Background poller adds items from subscribed feeds (configurable interval, default 1hr, feed URLs listed in config.yaml under `extraction.rss_feeds: []`) |

---

## 6. Feed & Publishing

### RSS 2.0 Feed

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>My Reading Feed</title>
    <link>https://username.github.io/audibleweb</link>
    <description>Articles converted to audio</description>
    <itunes:author>AudibleWeb</itunes:author>
    <itunes:image href="...cover.jpg"/>
    <item>
      <title>Article Title</title>
      <enclosure url="https://.../<slug>.mp3" length="..." type="audio/mpeg"/>
      <guid isPermaLink="true">https://.../<slug>.mp3</guid>
      <pubDate>Fri, 13 Jun 2026 10:00:00 GMT</pubDate>
      <itunes:duration>1845</itunes:duration>
      <link>https://original-article-url.com</link>
      <description>Source: original-article-url.com</description>
    </item>
  </channel>
</rss>
```

### Feed Rules
- Regenerated from job history on every publish (SQLite is source of truth)
- Episodes ordered newest-first
- Episode slug: sanitized title + date (`2026-06-13-article-title.mp3`)
- Validates against RSS 2.0 + iTunes podcast spec before push
- Feed metadata (title, description, cover) configurable in settings

### GitHub Pages Publisher Flow
1. Generate MP3 -> save to local `data/audio/`
2. Shallow clone gh-pages branch (`--depth 1`)
3. Copy MP3 to `audio/` in repo
4. Regenerate `feed.xml` from all episodes in DB
5. `git add` + `commit` + `push`
6. GitHub Pages serves updated feed within ~1 min

### Practical Details
- GitHub PAT stored in `.env`, scoped to repo
- Shallow clone keeps operations fast
- Audio files accumulate — user manages size by deleting old episodes from UI
- Feed URL: `https://<user>.github.io/<repo>/feed.xml`

---

## 7. Web UI & REST API

### REST Endpoints

```
POST   /api/jobs              — create job {input, type, voice_config?}
GET    /api/jobs              — list jobs (filterable by status)
GET    /api/jobs/:id          — job detail + status
POST   /api/jobs/:id/retry    — retry failed job
DELETE /api/jobs/:id          — delete job + audio + remove from feed

GET    /api/feed              — feed metadata + episode list
GET    /api/feed/url          — public feed URL
POST   /api/feed/republish   — force regenerate + push

GET    /api/voices            — available voices from TTS engine
GET    /api/settings          — current config
PUT    /api/settings          — update config

GET    /api/pronunciations    — list overrides
PUT    /api/pronunciations    — add/update
DELETE /api/pronunciations/:word — remove
```

### Web UI Pages

| Page | Purpose |
|------|---------|
| **Inbox** | Add URL/file/text. Drag-drop. Paste URL. Quick-add form. |
| **Queue** | Live job list with status badges. Progress for active job. Retry/delete. |
| **Feed** | Episode list. Play preview. Delete episodes. Copy feed URL. Health indicator. |
| **Settings** | Voice config, publisher config, LLM toggle, pronunciation editor. |

### Tech
- Flask + Jinja2 + HTMX (no JS framework)
- SSE for live job status updates
- Mobile-friendly for status checks from phone

### Auth
- No auth by default (local network)
- Optional API key for external triggers
- Config: `API_KEY=<token>` -> all API calls need `Authorization: Bearer <token>`

---

## 8. Configuration

### Hierarchy

```
.env                    — secrets (GitHub PAT, API keys, LLM endpoint)
config.yaml             — app settings
pronunciation.json      — pronunciation overrides
data/
  audibleweb.db         — SQLite
  audio/                — generated MP3s
```

### config.yaml

```yaml
feed:
  title: "My Reading Feed"
  description: "Articles converted to audio"
  cover: "cover.jpg"

voice:
  default: "af_heart"
  speed: 1.0

tts:
  engine: kokoro
  base_url: "http://localhost:8880/v1"
  max_parallel: 4

publisher:
  type: github_pages
  repo: "username/audibleweb-feed"
  branch: "gh-pages"

extraction:
  jina_fallback: true
  jina_api_key: ""
  rss_feeds: []            # URLs to poll for new articles
  rss_poll_interval: 3600  # seconds between polls

normalization:
  llm_enabled: true
  llm_base_url: ""
  llm_model: ""

server:
  host: "0.0.0.0"
  port: 5000
  api_key: ""
```

---

## 9. Error Handling & Reliability

**Principle: no silent failures.**

| Stage | Failure Mode | Response |
|-------|-------------|----------|
| Extraction | URL unreachable | Fail job: "Could not fetch URL" |
| Extraction | No readable content (<100 chars) | Fail job: "No extractable content" |
| Extraction | Jina fallback also fails | Fail job: "Extraction failed (both methods)" |
| Normalization | LLM unavailable | Skip normalization, continue. Log warning. |
| Normalization | LLM returns garbage | Discard output, use pre-normalization text. Log warning. |
| TTS | Engine unreachable | Fail immediately after health check |
| TTS | Chunk fails 3x | Fail entire job, report chunk index |
| TTS | Timeout (120s/chunk) | Count as failure, retry |
| Publishing | Git push auth failure | Fail at publish stage. Audio preserved. User fixes PAT, retries. |
| Publishing | Git push conflict | Force push (gh-pages is generated content) |
| Feed | Generated XML invalid | Validate before push. Invalid = fail, don't push. |

### Recovery
- On startup: intermediate-state jobs reset to `queued`
- Manual retry from UI/API. Retries from failed stage, not from scratch.
- Partial state preserved in DB.

### Observability
- Job history with timestamps per stage transition
- Last error visible in UI per job
- Feed health: last publish time, episode count, feed URL check
- SSE pushes status changes live

---

## 10. Testing Strategy

| Layer | Approach |
|-------|----------|
| Extractors | Integration tests against known URLs + fixture files |
| Text pipeline | Unit tests: input -> expected normalized output |
| TTS | Mock TTS server (returns silence). Test chunking, stitching, voice parsing. |
| Feed | Generate -> validate against RSS 2.0 spec programmatically |
| Publisher | Integration test with local git repo |
| API | Flask test client, full CRUD + job lifecycle |
| End-to-end | Fixture URL -> complete pipeline -> valid MP3 + valid feed entry |

CI runs on GitHub Actions. Kokoro not needed in CI — mock TTS. Feed validation on every PR.

---

## 11. Project Structure

```
audibleweb/
├── app.py                      # Flask app factory + startup
├── config.py                   # Load .env + config.yaml
├── api/
│   ├── routes.py               # REST endpoints
│   └── sse.py                  # Server-sent events
├── core/
│   ├── job_queue.py            # SQLite job manager
│   ├── pipeline.py             # Orchestrates extract->normalize->generate->publish
│   ├── text_pipeline.py        # Cleaning + LLM normalization + pronunciation
│   ├── tts.py                  # Voice parsing, async generation, stitching
│   └── feed.py                 # RSS generation + validation
├── extractors/
│   ├── base.py                 # Extractor protocol
│   ├── web.py                  # Trafilatura + Jina
│   ├── file.py                 # PDF/TXT/MD
│   └── rss.py                  # RSS feed import
├── engines/
│   ├── base.py                 # TTSEngine protocol
│   └── kokoro.py               # Kokoro via OpenAI-compatible API
├── publishers/
│   ├── base.py                 # Publisher protocol
│   ├── github_pages.py         # Git push to gh-pages
│   └── local.py                # Serve locally (dev)
├── plugins/                    # User-added (auto-discovered)
│   ├── extractors/
│   ├── engines/
│   └── publishers/
├── web/
│   ├── templates/
│   └── static/
├── data/                       # Runtime (gitignored)
│   ├── audibleweb.db
│   └── audio/
├── tests/
├── config.yaml
├── pronunciation.json
├── .env
├── pyproject.toml
├── Dockerfile
└── README.md
```

### Packaging
- PyPI-installable: `pip install audibleweb`
- Entry point: `audibleweb` launches Flask server
- Docker image for one-command deploy
- Core deps: Flask, trafilatura, httpx, spacy (optional), mutagen, feedgen

---

## 12. Non-Goals

- Multi-user / auth system beyond simple API key
- Chapter splitting (every input = one episode)
- Multi-voice narration (articles aren't dialogue)
- Emotion tags (overkill for articles)
- Real-time/streaming generation
- Mobile app (podcast app IS the client)
- EPUB as a first-class input (PDF/TXT/MD cover the file case)

---

## 13. Success Criteria

- [ ] Submit URL via web UI -> generates playable MP3 covering entire article
- [ ] Episode appears in feed.xml with correct title, enclosure, duration
- [ ] Feed validates and plays in Overcast when subscribed
- [ ] Broken input (empty page, unreachable URL) produces visible error, not silent no-op
- [ ] Full loop (submit -> episode in podcast app) requires zero manual steps after submit
- [ ] External trigger (bookmarklet/curl) creates job same as web UI
- [ ] Voice blending produces audibly different output from single voice
- [ ] LLM normalization correctly expands numbers/abbreviations in generated audio
- [ ] Failed job can be retried from UI without re-submitting
- [ ] App survives restart with jobs intact
