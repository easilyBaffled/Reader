# Voice blend builder — design

## Context

`lib/voice.py` already supports three voice-spec shapes, parsed/validated by
`parse_voice_spec()`:

- `"af_heart"` — single voice.
- `"af_heart+af_bella"` — native blend, up to 3 voices, equal weight (passed
  straight to the TTS API as one string).
- `"af_heart:0.7+af_bella:0.3"` — weighted blend, exactly 2 voices, weights
  summing to 1.0 (each voice synthesized separately, mixed via
  `mix_weighted_blend`).

The backend is more complete than the UI exposes:

- `config.voice.default` (`VoiceConfig.default`) is just a plain string field
  — any valid spec string already works as the global default with zero
  schema changes.
- `POST /api/jobs` already accepts a per-job `voice_config: {"voice": <spec>,
  "speed": <float>}` body, validated via `_validate_voice_config` →
  `parse_voice_spec`, stored as JSON on the job row, and consumed by
  `core/pipeline.py:50` (`voice_cfg.get("voice") or config.voice.default`).
  This path is only reachable via the JSON API today — the web Inbox form
  posts to `/web/jobs`, which doesn't handle `voice_config` at all.
- `GET /api/voices` already exists, calls `KokoroEngine.list_voices()`, and
  degrades gracefully (502 with an error body) if Kokoro is unreachable.
  Already tested (`test_list_voices`, `test_list_voices_unreachable`).

So the actual gap is UI-only, plus one small backend extension (`/web/jobs`
needs to accept `voice_config`, mirroring what `/api/jobs` already does).

## Goal

Let a user build a voice blend (single / native / weighted) through the
Settings tab (sets the global default) and, optionally, through the Inbox
tab (overrides it for one episode) — without ever needing to know the raw
spec-string syntax.

## Non-goals

- No changes to `lib/voice.py`, `parse_voice_spec`, `mix_weighted_blend`, or
  `KokoroEngine` — all already correct and tested.
- No new `/api/voices`-equivalent endpoint — the existing one is reused as-is.
- No JS test framework — this project is pure pytest with no Playwright/JS
  test runner; the new interactive JS gets verified by hand once built, not
  via new test infrastructure.

## Approach

### Shared component: the blend builder

One reusable Jinja partial (e.g. `partials/_voice_blend_builder.html`) plus
one small vanilla-JS file, in the same spirit as the existing drag-drop
overlay script already in `base.html` (this project accepts targeted vanilla
JS; it just avoids a JS framework, per `CLAUDE.md`).

Renders:
- Mode radios: **Single** / **Native blend** (≤3 voices, equal weight) /
  **Weighted blend** (exactly 2 voices, sliders).
- Up to 3 voice-picker slots. Each is a `<select>` populated by fetching
  `GET /api/voices` once on load; if that call fails (502, network error,
  whatever), every slot falls back to a plain text input (today's behavior)
  with a small inline note explaining why.
- Weighted mode shows exactly 2 slots with linked sliders — dragging one
  sets the other to `1 - value`, so the underlying weights always sum to
  1.0 by construction.
- A single hidden `<input>` that the JS keeps in sync with the resulting
  spec string (`af_heart`, `af_heart+af_bella`, or
  `af_heart:0.7+af_bella:0.3`). That hidden input is the only thing actually
  submitted — neither `apply_settings_patch` nor the job-creation routes
  need to know the builder exists; they just see a string, exactly as today.

### Settings tab

Replace the current free-text "Default voice" input with the builder,
hidden-field name unchanged (`voice[default]`) — `apply_settings_patch`
needs zero changes. On page load, the server parses the existing
`config.voice.default` via `parse_voice_spec` (already exists) and passes
the resulting `VoiceSpec` into the template so the builder opens already
showing the right mode/slots/weights, instead of always defaulting to
Single.

### Inbox tab

A collapsed `<details>` element ("Override voice for this episode") reveals
the same builder — no JS needed for the collapse/expand itself, that's
native `<details>` behavior. Its hidden field feeds a `voice_config` value
into the `/web/jobs` POST. Left collapsed/untouched → `voice_config` is
omitted from the POST entirely, and `pipeline.py`'s existing default-voice
fallback applies unchanged.

### Backend change: `/web/jobs` accepts `voice_config`

`create_job()` in `web/routes.py` currently only reads `input_value` /
`input_type` from the form. Extend it to also read a `voice_config` field
(JSON-encoded by the builder's JS, or simply absent if the override section
was never expanded), validate it with the existing `_validate_voice_config`
(imported from `api/routes.py`, same way `_job_to_dict` is already imported
there), and store it on the inserted row exactly like `/api/jobs` does.
Invalid input re-renders the Inbox partial with an error, same pattern as
the existing empty-input 422 case.

## Error handling

- Weighted-mode sliders are linked client-side, so an invalid weight sum
  can't be produced through the UI. Native mode's markup caps at 3 slots.
  Server-side `parse_voice_spec` / `_validate_voice_config` remains the
  authoritative backstop regardless of what the client sends.
- `GET /api/voices` failing degrades every picker slot to a text input —
  the page never hard-fails because Kokoro happens to be offline.
- Inbox override left collapsed → no `voice_config` in the POST → existing
  pipeline fallback to `config.voice.default` applies, unchanged behavior.

## Testing

- New tests on `/web/jobs` for `voice_config` accept / validate / store,
  mirroring the existing `/api/jobs` tests (`tests/test_api.py`'s
  `test_create_job_with_voice_config` and its invalid-voice / invalid-speed
  siblings — same shape, new target route).
- New tests asserting the blend-builder partial renders the right structure
  (mode radios present, correct slot count, hidden field present and
  correctly valued) for single / native / weighted `config.voice.default`
  values, in the same style as the existing `test_settings_shows_config_fields`.
- The interactive JS (slider linking, mode toggling, picker fallback) is
  verified manually (click through it once built) — no new JS test
  infrastructure, per Non-goals.
