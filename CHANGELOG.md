# Changelog

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
