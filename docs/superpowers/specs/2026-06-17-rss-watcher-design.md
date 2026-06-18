# RSS watcher — design

## Context

`docs/design.md` sec 5 (Triggers) and sec 8 (config.yaml) specify a
background poller that adds new items from subscribed RSS feeds as jobs, on
a configurable interval (default 1hr), feed URLs listed under
`extraction.rss_feeds: []`.

The backend is partially built already:

- `RSSImportExtractor.list_new_articles(feed_url, conn)`
  (`audibleweb/extractors/rss.py:66`) fetches a feed, filters out
  previously-seen entries (via `rss_seen_items` table, reader-whv), returns
  unseen entries as `Article` objects, and marks them seen before returning.
- `RSSImportExtractor.first_subscribe(feed_url, conn)` (same file, line 56)
  marks all current entries seen and creates 0 jobs — the documented
  first-sync behavior so subscribing to a feed doesn't backfill its entire
  history.
- `config.extraction.rss_feeds: list[str]` and
  `config.extraction.rss_poll_interval: int` (`audibleweb/config.py:59-60`)
  already exist as config fields.

None of this is wired up: nothing calls `list_new_articles` or
`first_subscribe` outside tests, there's no scheduler, and there's no UI/API
to add a feed URL to `rss_feeds` in the first place. This spec covers the
three missing pieces: the poll loop, job creation from new items, and
subscribe/unsubscribe.

## Goal

Add a feed URL once. From then on, new entries in that feed become episodes
automatically, with no manual "import this article" step.

## Non-goals

- No per-feed poll interval — one global `rss_poll_interval` applies to all
  feeds (matches the spec's singular wording; per-feed override is
  unrequested complexity).
- No manual "poll now" endpoint — wait for the interval. Can be added later
  if it turns out to matter.
- No cleanup of `rss_seen_items` rows when a feed is unsubscribed — orphaned
  rows are harmless, and the table is keyed by `(feed_url, item_id)` so
  re-subscribing later just re-triggers `first_subscribe` semantics for that
  URL again.
- No changes to `RSSImportExtractor`, `rss_seen_items`, or the dedup
  functions — all already correct and tested (reader-whv).

## Approach

### Poll loop: extend the existing worker, no new thread

`Worker._main()` (`audibleweb/worker.py:70`) already runs one continuous
asyncio event loop in a dedicated thread, polling the `jobs` table every
`poll_interval` seconds (default 1s) for as long as the app runs. Piggyback
the RSS check on that same loop instead of spinning up a second
thread/scheduler:

- Add `self._last_rss_poll: float = 0.0` to `Worker.__init__`.
- After the existing `_claim_next_job` check in the main loop, add: if
  `time.monotonic() - self._last_rss_poll >= config.extraction.rss_poll_interval`,
  call a new `_poll_rss_feeds(conn, config)` helper, then update
  `self._last_rss_poll = time.monotonic()`.
- `_last_rss_poll` is in-memory only — on restart it's `0.0`, so the first
  loop iteration polls immediately regardless of how long it's been. This is
  intentional: the dedup table makes an extra-early poll harmless (no
  duplicate jobs), so there's no need for persisted poll-timestamp state.

### `_poll_rss_feeds`: new item → job

New function in `audibleweb/worker.py` (or a small new module if it grows,
but it's a handful of lines):

```python
async def _poll_rss_feeds(conn, config) -> None:
    extractor = RSSImportExtractor()
    for feed_url in config.extraction.rss_feeds:
        try:
            articles = await extractor.list_new_articles(feed_url, conn)
        except ExtractionError as exc:
            logger.warning("rss poll failed for %s: %s", feed_url, exc)
            continue
        for article in articles:
            if not article.source_url:
                logger.warning("rss entry from %s has no link, skipping", feed_url)
                continue
            _insert_job(conn, input_type="url", input_value=article.source_url)
```

Each new entry becomes an `input_type="url"` job using the entry's link
(`article.source_url`), not the RSS-supplied summary/content. This reuses
the existing `WebExtractor` (trafilatura + Jina fallback) for full-article
text — many feeds only ship truncated summaries, and routing through the
normal url pipeline means **zero changes to `core/pipeline.py`**: an
RSS-discovered job is indistinguishable from one a user pasted into the
Inbox. Entries with no link (rare — `_entry_id` in rss.py already treats
link-less entries as an edge case) are skipped and logged, not retried.

`_insert_job` is the same `INSERT INTO jobs (...) VALUES (...)` shape
`POST /api/jobs` already uses — extract today's inline insert in
`api/routes.py` into a shared helper if that's cleaner, or just duplicate
the four-line insert; decide at implementation time based on how it reads.

### Error handling: one bad feed doesn't stop the others

A feed that's unreachable or unparseable raises `ExtractionError` from
`list_new_articles`'s internal `_fetch`/`feedparser.parse` calls. Caught
per-feed inside the loop above — log a warning, move to the next feed_url,
never crash the worker loop or block normal job processing. This matches
the existing graceful-degradation convention used for LLM normalization
failures (docs/design.md sec 9).

### Subscribe / unsubscribe: dedicated endpoints, not the settings-patch form

`rss_feeds` is a `list[str]`. The existing generic settings-patch mechanism
(`_parse_settings_form` in `web/routes.py:187`) only coerces scalar
dataclass fields (str/int/float/bool) keyed `section[field]` — it has no
concept of list add/remove. Rather than bolt list-handling onto that
generic path, give feed subscriptions their own small CRUD, mirroring how
pronunciations already get dedicated endpoints despite living outside
`config.yaml`:

- `GET /api/feeds` — list `config.extraction.rss_feeds`.
- `POST /api/feeds {"url": "..."}` — validate the URL is non-empty and not
  already subscribed (400 if so), append to `rss_feeds`, persist via the
  existing `apply_settings_patch` config-write path, then call
  `await extractor.first_subscribe(url, conn)` (same `asyncio.run()` pattern
  `GET /api/voices` already uses to call async code from a sync Flask route —
  `api/routes.py:267`). Returns the count of existing items marked seen.
- `DELETE /api/feeds {"url": "..."}` — remove from `rss_feeds`, persist. 404
  if not subscribed. Body-based (not `/api/feeds/<url>`) because a feed URL
  contains slashes that Flask's default path converter can't round-trip.

Matching `/web/feeds` HTMX routes (`web/routes.py`) plus a new partial
(e.g. `partials/rss_feed_list.html`, following `pronunciation_list.html`'s
pattern) render a small list-plus-add-form in the **Settings tab**, as a
new section under the existing Extraction settings. Each row shows the feed
URL and a delete button; the add form is a single URL input.

## Testing

- `tests/test_worker.py`: unit test the poll-due check with a fake/injected
  clock (due → polls, not due → skips), and `_poll_rss_feeds` with a mocked
  `RSSImportExtractor` (new articles → jobs inserted with correct
  `input_type`/`input_value`; no-link article → skipped; `ExtractionError`
  on one feed → other feeds still processed).
- `tests/test_api.py`: `/api/feeds` CRUD — add (asserts `rss_feeds` updated
  + `first_subscribe` called + seen-count returned), duplicate add (400),
  remove via body-based DELETE, remove-nonexistent (404) — same
  temp-config-file pattern existing settings tests use.
- `tests/test_web_ui.py`: Settings tab renders the new feed list section
  with current `rss_feeds`, add/delete HTMX wiring present.
- No live network — feed fetches mocked via `httpx.MockTransport`, same
  pattern as `tests/test_rss_extractor.py`.
