# Changelog

## 1.4.0
- New "Training Log" page (linked from the header): pick an activity type,
  choose a period (last 7 days, last 4 weeks, or last year), and navigate
  forward/backward through periods. The 1-year view groups activities by
  month with distance totals instead of listing them directly - click a
  month to drill into its individual activities, with previous/next month
  navigation (correctly disabled for months that haven't started yet).
  Always shows total distance, average weekly, and average monthly distance
  for whatever's currently displayed. Verified all the date-window
  arithmetic (7d/4w/1y window boundaries, month rollover including
  December->January, leap year February, future-month guarding) via
  dedicated tests before shipping, since that's exactly the kind of logic
  that's easy to get subtly wrong.

## 1.3.1
- Fixed sort direction being stuck on descending - clicking an
  already-sorted column now correctly toggles between ascending and
  descending instead of always landing back on descending
- Added a pace filter (min/km range) alongside the existing distance and
  duration filters
- Set the real repository URL and maintainer info for the Home Assistant
  app listing

## 1.3.0
- Fixed Date/Pace (and all other) table columns wrapping onto two lines -
  table cells no longer wrap at any screen size; the existing horizontal
  scroll wrapper handles any overflow instead
- All activity list tables (home page and each route's page) now support
  clicking a column header to sort by it (Date, Name, Type, Distance,
  Duration, Pace), plus per-column filters: name contains, date range,
  distance range, duration range - in addition to the existing type filter
  on the home page. Sort/filter state persists across pagination and page
  size changes
- Added a pace-over-time chart to each route's page, similar to Strava's -
  plots pace per run chronologically with the faster pace higher on the
  chart, hoverable for exact date/pace, and clickable through to that run.
  Implemented as a small hand-rolled SVG (no new dependency added)

## 1.2.2
- The mobile-detection JS from 1.1.1 was inline, which Home Assistant's
  ingress iframe may block via Content-Security-Policy while still allowing
  external same-origin scripts (Leaflet's own script kept working, which is
  what pointed at this). Moved it to its own static file
  (`mobile-detect.js`) referenced via `<script src>` instead, which should
  be far more likely to actually execute under ingress. Note: the 1.2.1
  table-width fix was a real bug but not the actual cause of the
  ingress-specific issue, since that CSS applies identically regardless of
  deployment method - it's still a valid fix, just not *the* fix for this.

## 1.2.1
- Fixed the real cause of tables not going responsive on mobile: `.table`
  had `width: 100%` which clamped it to its container, so the nowrap cells
  meant for horizontal scrolling were just getting squished into that fixed
  width instead of the table growing past it and actually triggering the
  scrollable overflow. Tables now correctly widen to their content and
  scroll horizontally within their own row on narrow screens. Also added a
  page-level `overflow-x: hidden` safety net so the page itself never
  scrolls sideways regardless of what's inside it.

## 1.2.0
- Route matching is now incremental for routine imports (Garmin auto-sync,
  manual Garmin/Strava sync, and small file uploads): instead of
  recomputing every pairwise comparison across your entire history on every
  sync, only the newly-added activities are compared against what's
  relevant. This was previously the main performance cost of background
  Garmin sync on slower hardware (e.g. Raspberry Pi). Manual actions
  (Recompute, dedupe, type normalization) and large bulk imports (20+ new
  activities at once) still use the full, guaranteed-correct rebuild.
  Verified the incremental result is mathematically identical to a full
  rebuild via automated tests before shipping this.

## 1.1.1
- Fixed responsive/mobile layout not activating when accessed through Home
  Assistant's ingress iframe (worked fine via direct docker compose access).
  Mobile styling now also driven by an actual measured-width JS check, not
  just a CSS @media breakpoint, since some iframe contexts don't evaluate
  @media queries against the true device width.

## 1.1.0
- Home page is now the "All Activities" list (was previously a separate
  page at /activities)
- Import/sync actions (upload, Strava, Garmin, cleanup tools) moved to a
  dedicated "Import & Sync" page
- Logging now includes timestamps on every line, and scheduled Garmin
  background sync checks are logged every cycle (not just when something
  changed)
- Responsive design pass: tables scroll horizontally instead of overflowing
  on narrow screens, tighter spacing and layout adjustments below 640px

## 1.0.0
- Initial release: GPX/FIT/TCX import, route matching, Strava sync, Garmin
  auto-sync (including web-based MFA login), activity type cleanup tools,
  cross-source deduplication, pagination, Home Assistant app packaging
