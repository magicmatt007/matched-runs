# Changelog

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
