# Breadth extractor QA — design

## Context

The URL input path (`extractors/web.py`) was validated end-to-end this session:
extract → TTS → stitch → publish, with two real bugs found and fixed (trafilatura
API misuse, and a relative-path bug in `send_from_directory`). Three other
extractors exist in the codebase but have never been exercised: `raw_text`,
`file` (txt/md/pdf), and `rss`.

While investigating how to reach these through the live UI, two gaps surfaced:

- **File upload**: `FileExtractor.extract()` expects `input_value` to be a
  filesystem path string (`Path(input).suffix`), not uploaded file bytes. The
  Inbox UI advertises "drag and drop a PDF/TXT/MD file" but there is no file
  input element, no drop handler, and no JS at all (the stack is HTMX-only,
  confirmed zero `.js` files under `static/`). There is no multipart upload
  route in `api/routes.py` or `web/routes.py`.
- **RSS**: `config.extraction.rss_poll_interval` exists in config but nothing
  reads it — no scheduler or background poll loop. `RSSImportExtractor` works
  if handed a feed URL directly, but there is no "subscribe" UI action and no
  polling process to surface new items automatically.

Both extractor classes are functionally complete; only the UI/scheduling
wiring to reach them is missing.

## Goal

Verify the extraction logic itself for `raw_text`, `file:txt`, `file:md`,
`file:pdf`, and `rss` — independent of the missing UI/poller — using the same
fix-and-verify loop already used for the URL path this session (systematic
debugging + TDD regression test per bug found). Explicitly defer the UI/poller
gap rather than building it now.

## Non-goals

- Building the file-upload route or drag-drop UI.
- Building an RSS subscribe action or background poller.
- Running every input type through the full TTS/stitch/publish pipeline —
  that stage is already proven via the URL run and is identical regardless of
  which extractor produced the text. CPU Kokoro at `max_parallel: 1` makes a
  full run ~10 minutes each; not worth repeating 5x for stages that don't
  vary by extractor.

## Approach

### Fixtures

- `raw_text`: inline string, no fixture file needed.
- `file:txt` / `file:md`: two small fixture files (~200 words each, comfortably
  over `MIN_CONTENT_CHARS`), written to a tmp/fixtures location.
- `file:pdf`: a synthetic PDF generated with `fitz` (PyMuPDF, already a
  dependency of `file.py`) — no new dependency needed.
- `rss`: a real, stable public RSS feed URL. `RSSImportExtractor._fetch` does
  a real HTTP GET; standing up a fake feed server is unnecessary overhead for
  this pass.

### Procedure (per extractor)

1. Create a job via direct API call (`POST /api/jobs` or `/web/jobs`) with an
   explicit `input_type`, bypassing the web form's auto-detect (the known UI
   gap) — pass the fixture as `input_value`.
2. Poll job status via the jobs table until it reaches `generating` (extraction
   succeeded, pipeline moved on) or `failed`.
3. On `failed`: read the full traceback from `logs/audibleweb.log`, root-cause
   it (systematic-debugging skill), write a failing regression test first
   (TDD), fix, re-verify GREEN, re-run the live job to confirm.
4. On `generating`: sanity-check the DB's extracted `title`/`word_count` for
   plausibility, then move to the next input type without waiting for TTS to
   finish.

### Gap tracking

File a beads issue (or two) for "file upload UI" and "RSS subscribe UI +
poller" as known, deliberately-deferred gaps — not addressed in this pass.

## Success criteria

All 5 cases (raw_text, file:txt, file:md, file:pdf, rss) reach `generating`
status with extracted text/title that plausibly matches the fixture content.
Any bug found along the way gets a regression test and a fix, held to the same
bar as the URL-path fixes this session (root-cause before patch, test before
fix, full suite green afterward).

## Testing

Existing `uv run pytest` suite must stay green throughout. New regression
tests follow the same per-bug pattern already established in `test_app.py`
and `test_web_extractor.py` this session: reproduce against real
library/filesystem behavior (not over-mocked), watch it fail for the right
reason, fix, watch it pass.
