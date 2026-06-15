# AudibleWeb

Multi-format inbox (URL, PDF, TXT, MD, pasted text, RSS) → text pipeline → TTS → podcast RSS feed.

## Stack

- **Language:** Python 3.12+
- **Package manager:** uv
- **Web framework:** Flask + Jinja2 + HTMX (no JS framework)
- **TTS engine:** Kokoro (OpenAI-compatible API) — pluggable
- **Database:** SQLite (job queue, history)
- **Audio:** FFmpeg for stitching (not PyDub — memory efficient)
- **Text extraction:** Trafilatura + Jina Reader API fallback
- **LLM normalization:** OpenAI-compatible API (optional)
- **Publishing:** GitHub Pages (git push MP3 + feed.xml to gh-pages)
- **Real-time updates:** SSE

## Commands

```bash
uv run python -m audibleweb        # run dev server
uv run pytest                      # run tests
uv run pytest tests/unit           # unit tests only
uv run pytest tests/integration    # integration tests
uv run ruff check .                # lint
uv run ruff format .               # format
```

## Architecture

Plugin-based with Python Protocols:
- `extractors/` — URL, file, RSS, raw text
- `engines/` — TTS engines (Kokoro default)
- `publishers/` — GitHub Pages, local

Pipeline: extract → clean → normalize (LLM, optional) → pronunciation → chunk → TTS → stitch → publish

Job queue: SQLite, one job at a time, parallelism within-job at chunk level.

## Conventions

- Async where I/O bound (TTS calls, HTTP fetches)
- Plugin discovery: drop .py in `plugins/{extractors,engines,publishers}/`
- Config hierarchy: `.env` (secrets) → `config.yaml` (settings) → `pronunciation.json`
- REST API is source of truth — web UI hits same endpoints
- No silent failures — every error surfaces to user
- All-or-nothing per episode (partial output never published)

## Key Design Decisions

- Every input = one episode (no chapter splitting)
- Single voice per episode (articles aren't dialogue)
- No emotion tags (overkill for articles)
- Voice blending supported (up to 3 native, 2 weighted)
- LLM normalization gracefully degrades if unavailable
- Jobs persist across restarts; intermediate-state resets to queued on startup

## Testing

- Mock TTS server in tests (returns silence)
- Feed validation against RSS 2.0 spec
- Flask test client for API tests
- Fixture-based for extractors and text pipeline

## Design Spec

Full spec: see `docs/design.md`

## gstack (REQUIRED — global install)

**Before doing ANY work, verify gstack is installed:**

```bash
test -d ~/.claude/skills/gstack/bin && echo "GSTACK_OK" || echo "GSTACK_MISSING"
```

If GSTACK_MISSING: STOP. Do not proceed. Tell the user:

> gstack is required for all AI-assisted work in this repo.
> Install it:
> ```bash
> git clone --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
> cd ~/.claude/skills/gstack && ./setup --team
> ```
> Then restart your AI coding tool.

Do not skip skills, ignore gstack errors, or work around missing gstack.

Using gstack skills: After install, skills like /qa, /ship, /review, /investigate,
and /browse are available. Use /browse for all web browsing.
Use ~/.claude/skills/gstack/... for gstack file paths (the global path).

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
