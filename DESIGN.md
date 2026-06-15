# AudibleWeb — Design System

Source of truth for AudibleWeb's UI. Locked via `/plan-design-review` (2026-06-15,
score 3/10 → 8/10, decisions D2-D10, NO UNRESOLVED DECISIONS). This file codifies
those decisions as implementable tokens and fills the gaps the review didn't drill
into (state colors, type scale, radius/shadow/motion, icons).

**Memorable thing:** paste a link, walk away, find a podcast episode waiting. The UI's
job is to make that queue feel alive and trustworthy — never a black box.

---

## 1. Design Tokens (CSS custom properties)

Drop into `web/static/css/tokens.css`, loaded before component styles.

```css
:root {
  /* Color — surfaces */
  --bg: #1a1a1a;
  --surface: #262626;
  --surface-hover: #303030;
  --surface-active: #383838;
  --border: #3a3a3a;

  /* Color — text */
  --text: #e5e5e5;
  --text-muted: #a3a3a3;
  --text-disabled: #6b6b6b;

  /* Color — accent (interactive) */
  --accent: #3b82f6;          /* links, focus rings, progress fill, icons */
  --accent-strong: #2563eb;   /* filled buttons w/ white text (4.5:1+) */
  --accent-subtle: rgba(59, 130, 246, 0.12); /* active tab bg, badge bg */

  /* Color — status */
  --success: #22c55e;
  --success-subtle: rgba(34, 197, 94, 0.12);
  --error: #ef4444;
  --error-subtle: rgba(239, 68, 68, 0.12);
  --warning: #f59e0b;
  --warning-subtle: rgba(245, 158, 11, 0.12);

  /* Typography */
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', monospace;

  /* Spacing scale (locked: 4/8/12/16/24/32/48) */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 12px;
  --space-base: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --space-2xl: 48px;

  /* Radius */
  --radius-sm: 4px;   /* inputs, small badges */
  --radius-md: 8px;   /* job cards (locked) */
  --radius-lg: 12px;  /* modals, settings panels */
  --radius-full: 999px; /* pill badges */

  /* Elevation (dark theme — border + soft shadow, not heavy drop shadows) */
  --shadow-card: 0 1px 2px rgba(0, 0, 0, 0.3);
  --shadow-elevated: 0 4px 12px rgba(0, 0, 0, 0.4);

  /* Motion */
  --transition-fast: 120ms ease;     /* hover states */
  --transition-base: 200ms ease;     /* tab switches, HTMX swaps */
  --transition-progress: 300ms ease-out; /* progress bar fill */

  /* Focus */
  --focus-ring: 0 0 0 2px var(--bg), 0 0 0 4px var(--accent);
}
```

**Why these additions over the locked baseline:**
- `--surface-hover` / `--surface-active` — every interactive surface (job card rows,
  tab items, settings rows) needs a hover/press state; deriving from `--surface`
  keeps it coherent instead of ad-hoc per component.
- `--accent-strong` — white text on `--accent` (#3b82f6) is ~3.7:1, fails AA normal-text
  contrast (4.5:1). Filled buttons use `--accent-strong` (#2563eb, ~4.6:1). `--accent`
  stays for links, icons, focus rings, progress fill — non-text UI elements only need 3:1.
- `--warning` — the eng review (D6) added worker heartbeat stall detection
  (>60s idle = stalled job), but the original badge spec only covered
  queued/in-progress/done/failed. Stalled needs its own color so a user scanning
  the queue can tell "stuck" from "still working."

---

## 2. Typography

Loaded via Google Fonts CDN (locked decision D6).

| Role | Font | Size | Weight | Line-height |
|---|---|---|---|---|
| Page title (h1) | Inter | 24px | 600 | 1.3 |
| Section / tab label (h2) | Inter | 18px | 600 | 1.4 |
| Body / UI text | Inter | 15px | 400 | 1.5 |
| Small / meta (timestamps, captions) | Inter | 13px | 400 | 1.4 |
| Technical data (durations, chunk counts, file sizes) | JetBrains Mono | 13px | 500 | 1.4 |

Rule of thumb: **if it's a number a user might compare or copy (duration, MB, chunk
`12/47`), it's mono.** Everything else is Inter.

---

## 3. Iconography

**Lucide** (MIT, inline SVG, no JS framework needed). 20px default, 1.5px stroke,
`currentColor` so icons inherit text/status colors automatically.

Why Lucide over Heroicons: Lucide's thinner stroke pairs better with Inter's
lighter weights and reads as a *dev tool*, not a consumer app — fits a personal
queue-management UI better than Heroicons' rounder, friendlier shapes.

Icons needed: play, pause, retry (rotate-cw), delete (trash-2), copy (for feed URL),
upload/drop (upload-cloud), settings (sliders), rss, file-text, link, check-circle
(done), x-circle (failed), alert-triangle (stalled).

---

## 4. Layout (locked)

- **Single page, tab sections**: Inbox | Queue | Feed | Settings. Default tab: Queue.
- Instant HTMX tab switches, no page reload — use `--transition-base` for the
  content swap fade.
- **Desktop (>768px)**: tabs at top, horizontal tab bar with 2px accent underline
  on active tab.
- **Mobile (≤768px)**: tabs become bottom nav (fixed), job cards stack full-width,
  quick-add input full-width above the active tab content.
- Touch targets: 44px minimum.

---

## 5. Components

### Status badge
Pill (`--radius-full`), padding `--space-xs --space-sm`, Inter 13px/500.

| Status | Background | Text/icon | Notes |
|---|---|---|---|
| `queued` | `--surface` | `--text-muted` | neutral, no animation |
| `extracting` / `normalizing` / `generating` / `publishing` | `--accent-subtle` | `--accent` | subtle pulse animation (respects `prefers-reduced-motion`) |
| `done` | `--success-subtle` | `--success` | check-circle icon |
| `failed` | `--error-subtle` | `--error` | x-circle icon |
| `stalled` | `--warning-subtle` | `--warning` | alert-triangle icon — worker heartbeat >60s |

### Progress bar
- Track: `--surface`, height 6px, `--radius-full`.
- Fill: `--accent`, width transition via `--transition-progress`.
- Label below: stage text (Inter 13px, `--text-muted`) + mono chunk counter
  (`12/47`) + elapsed/estimated remaining time (mono).

### Job card
- Background `--surface`, `--radius-md`, border 1px `--border`.
- Padding `--space-lg` desktop / `--space-base` mobile.
- Active job card (Queue tab, top of hierarchy): `--shadow-elevated`, expanded —
  shows progress bar + stage label + chunk counter.
- Completed/failed rows below: compact, `--shadow-card`, title + duration (mono) +
  status badge + action icons (play/retry/delete).
- Hover: `--surface-hover`. Active/pressed: `--surface-active`.

### Tab bar
- Horizontal (desktop) / bottom nav (mobile).
- Active: `--text` + 2px `--accent` underline (desktop) or `--accent` icon+label
  (mobile bottom nav).
- Inactive: `--text-muted`.
- Transition: `--transition-fast` on hover/active swap.

### Quick-add input
- Full-width, `--surface` background, border 1px `--border`, `--radius-md`.
- Focus: border `--accent` + `--focus-ring`.
- Placeholder: "Paste a URL, or drop a PDF/TXT/MD file..."
- Always visible on Inbox and Queue tabs (locked: "Quick-add input always visible").

---

## 6. Interaction States (locked, restated for reference)

| Tab | Loading | Empty | Error | Success | Partial |
|---|---|---|---|---|---|
| Queue | Skeleton rows (`--surface`, shimmer) | "Paste a URL above to create your first episode" + arrow to input | Inline error banner (`--error-subtle` bg, `--error` text) + retry button | Job card with play button, `done` badge | Active job + completed list below |
| Feed | Skeleton | "No episodes published yet. Process a job first." | Connection error banner | Episode list with play buttons | Some episodes ready, others still processing (badge shows status) |
| Inbox | — | Quick-add input ready, empty state copy below it | Invalid URL/file: inline error text under input, `--error` | "Job created!" flash (`--success-subtle`) → auto-switch to Queue | — |
| Settings | — | Config pre-populated from `config.yaml` | Field-level validation errors, `--error` text below field | "Saved" flash (`--success-subtle`), auto-dismiss 3s | — |

**Skeleton shimmer**: animated gradient sweep `--surface` → `--surface-hover` →
`--surface`, 1.5s loop, disabled under `prefers-reduced-motion`.

---

## 7. Progress Communication (locked)

Stage labels, shown above the progress bar on the active job card:

1. "Extracting article..."
2. "Generating audio 12/47..." (chunk counter, mono)
3. "Publishing to feed..."

Plus: elapsed time counter (mono), estimated remaining
(`chunks_done/total * avg_chunk_time`, mono). On completion: "Episode ready! Added
to your feed." (`--success`) + play-preview button. First-time users see one line
of plain-English context under the stage label (dismissable, stored in
localStorage so it doesn't repeat).

---

## 8. Drag & Drop (locked)

- Full-page drop zone active on every tab.
- Drag-over: full-page overlay, `--bg` at 90% opacity, centered "Drop file to
  create episode" (Inter, 18px, `--text`).
- Accepted types: PDF, TXT, MD.
- Invalid type: overlay text switches to "Unsupported file type" in `--error`.
- Release: creates job immediately, switches to Queue tab.

---

## 9. Accessibility (locked + expanded)

- Contrast: body text `--text` on `--bg` is ~13:1 (well above 4.5:1 AA). Filled
  buttons use `--accent-strong` for white-text contrast (~4.6:1). `--accent` alone
  (3.7:1 with white) is reserved for non-text UI (icons, borders, progress fill,
  focus rings — 3:1 AA requirement for UI components).
- Focus: visible 2px `--accent` ring (`--focus-ring`) on every interactive element,
  keyboard-navigable tab order (Tab through sections, Enter to submit/activate).
- ARIA labels on all icon-only buttons (play, pause, retry, delete, copy feed URL).
- Semantic HTML: `<nav>` for tab bar, `<main>` per tab panel, `<section>` per job
  card group.
- `prefers-reduced-motion`: disable skeleton shimmer, status-badge pulse, and
  progress-bar transition (snap to value instead of animating).

---

## 10. Implementation Notes

- All tokens are CSS custom properties on `:root` — no preprocessor needed, matches
  the "no JS framework" stack (Flask + Jinja2 + HTMX).
- Dark theme only (locked D9) — no `prefers-color-scheme` branching, no light theme
  tokens to maintain.
- Icons: vendor Lucide SVGs as Jinja includes (`{% include "icons/play.svg" %}`)
  or inline via a small `icon(name)` Jinja macro — keeps bundle dependency-free.
