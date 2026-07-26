# Changelog

## 1.9.0
- Database import now runs as a background job with a real progress bar
  too (writing the file in 1MB chunks to report actual byte-level
  progress, then quick indeterminate phases for validating/replacing/
  migrating), same reasoning as the file upload and Garmin sync progress
  from 1.8.0 - useful since a 100MB+ database can genuinely take a while to
  write on slow storage. Removed the old static "database imported" banner
  in favor of this job-based one.
- Activity detail pages now show Previous/Next buttons when reached from a
  list (the home "All Activities" page, a matched route's page, or the
  Training Log), navigating within that exact same sort/filter/context -
  not some arbitrary global order. Clicking Next repeatedly keeps working
  correctly since the context carries forward on the buttons themselves,
  not just the initial link into the activity. No buttons shown when an
  activity is reached without list context (e.g. a direct bookmark).
  Verified the underlying prev/next index logic against several edge cases
  (first item, last item, single-item list, activity no longer in the
  list) before wiring it up.

## 1.8.1
- Fixed activities.csv-only uploads (re-uploading just the CSV, without the
  activity files, to backfill names after the fact) doing nothing despite
  logging "Loaded N titles" - a regression from 1.8.0's background-job
  refactor: the route returned early whenever there were no other files in
  the upload, before ever spawning the background job that actually
  applies the name backfill. Uploading the CSV alone now correctly
  triggers that backfill.
- Manual "Sync from Garmin now" now runs as a background job with the same
  live progress UI the bulk import got in 1.8.0 (connecting → checking for
  new activities → syncing N/M → matching, then a done/error summary) -
  previously this blocked the request with zero feedback, which was most
  noticeable on a first sync pulling in a lot of history at once.

## 1.8.0
- Uploads now run as a real background job instead of blocking the whole
  request until finished: the Import & Sync page shows a live progress bar
  with an estimated time remaining, covering both the file-parsing phase
  and (for large imports) the route-matching phase, which is often the
  slower part. The import now genuinely continues running server-side if
  you navigate away or close the browser entirely - previously this was
  ambiguous at best, since the whole thing lived inside a single blocking
  request.
  - Runs on a background thread (not just an async task) specifically so
    the CPU-bound matching step doesn't freeze the progress-polling
    endpoint itself while it's computing.
  - Route matching (`rebuild_groups`) now accepts an optional progress
    callback, invoked periodically during the O(n^2) comparison loop -
    verified this reports accurate, monotonically-increasing progress
    without slowing down the actual matching, using a mock database with
    60 activities across 20 real-ish routes.
  - Verified the ETA calculation (rate-based projection from elapsed time
    and fraction complete) against concrete before/after scenarios rather
    than just eyeballing the formula.
  - Fixed a task-lifetime correctness issue while building this: neither
    this new import task nor the existing Garmin background sync task were
    keeping a reference to themselves, which Python's own asyncio
    documentation warns can lead to a task being silently garbage-collected
    mid-execution. Both now hold a reference until they finish.
  - Known limitation: since the import holds a long-running database
    transaction, triggering another write action (Recompute, Garmin sync,
    dedupe) at the exact same time could occasionally hit a SQLite lock.
    Not solved with a full cross-route locking system for what should be a
    rare edge case in a single-user app - just avoid clicking other
    actions while a large import is actively running.

## 1.7.0
- Indoor activities with no GPS data (pool swims, gym workouts, treadmill
  sessions) are now imported instead of being skipped with "No GPS points
  found" - they just can't be route-matched, for the obvious reason there's
  no route. Distance, duration, heart rate, cadence, and calories are all
  still captured normally.
  - Found and fixed a real bug while building this: a pool swim's
    TCX/FIT distance and start time were being silently dropped entirely
    (not just the GPS track) because the code only recorded them for
    trackpoints that also had a GPS position - which indoor trackpoints
    never do. Fixed to capture time/distance independently of whether
    position data is present.
  - Found and fixed a genuine crash risk: comparing two indoor activities
    of the same type (e.g. two pool swims) during route matching would
    divide by zero, since both would have an empty GPS track but a real
    non-zero recorded distance, bypassing the existing "zero-length track"
    guard. Activities with no GPS track are now excluded from every
    matching/dedup code path entirely (rebuild, incremental matching,
    cross-source duplicate detection, and the manual dedupe tool) - not
    just handled safely, but never compared at all, since there's no
    meaningful route comparison to make anyway. Verified this crash
    actually reproduces before the fix and is fully excluded after.
  - The activity detail page shows a clear "no GPS track" message instead
    of an empty/broken map for these activities, and doesn't claim "no
    matches found yet" (which implies future matching potential that
    doesn't exist here).

## 1.6.2
- Fixed TCX/GPX import failing with "XML or text declaration not at start
  of entity" on some Strava export files - traced to a quirk in Strava's
  own export tooling that adds a few stray leading whitespace characters
  before the `<?xml ...?>` declaration, which strict XML parsers reject
  outright (the spec requires it to be the very first thing in the file).
  Now strips anything before the first `<` before parsing. Verified against
  the actual reported file (confirmed the exact same error reproduces
  without the fix, and that real data - 4594 GPS points, elevation,
  calories - extracts correctly with it).

## 1.6.1
- Documented the Home Assistant ingress 16MB request-body limit (a hardcoded
  Supervisor restriction, not something this app can fix) that breaks
  importing a large database through the sidebar - added to DOCS.md, and
  also as a context-aware in-app warning on the Import & Sync page that
  only appears when you're actually viewing through ingress, pointing you
  at the direct port for that specific action instead.

## 1.6.0
- Now captures and shows elevation gain/loss, average/max heart rate,
  average cadence, and calories on the activity detail page (only the
  fields actually available for that activity are shown - most sources
  won't have all of them).
  - **FIT files**: prefers the device-computed session totals for
    ascent/descent/HR/cadence/calories; falls back to deriving
    elevation from point-level altitude if the session summary omits it.
  - **TCX files**: heart rate/cadence from trackpoints (aggregates
    correctly across multiple laps), calories/HR fallback from lap
    summaries, elevation derived from trackpoint altitude.
  - **GPX files**: elevation always available if the file has it; heart
    rate/cadence only if the exporting device included Garmin's
    TrackPointExtension - hit or miss depending on the export, unlike
    FIT/TCX where it's more consistently present.
  - **Garmin live sync**: pulls average/max heart rate, cadence, and
    calories directly from Garmin's own activity summary (confirmed
    field names against real API response samples); elevation gain/loss
    field names are best-effort and fall back to GPX-derived values if
    absent.
  - **Strava sync**: elevation gain, average/max heart rate, and average
    cadence from Strava's documented summary activity fields. No
    elevation loss or calories (Strava doesn't expose loss in the
    summary, and calories needs a separate per-activity API call we're
    not making to avoid multiplying request volume per sync).
  - Existing already-imported activities won't have these until
    re-synced or re-uploaded - same backfill-on-reimport pattern used
    when activity_type was added.
  - Verified the elevation gain/loss math and the TCX field extraction
    (multi-value HR/cadence averaging, lap-level calories) against
    synthetic test files before shipping.

## 1.5.1
- Training Log 1-year view now includes the current in-progress month
  (e.g. on any day in July, shows August last year through July this year)
  instead of ending at the last complete month
- Shortened the 1-year chart's month labels to 3 letters (Jul, Aug, ...)
  instead of the full name - the overview cards still show the full month
  name, only the chart axis changed. Verified that 12 consecutive months
  always produce 12 distinct 3-letter labels (no year needed to
  disambiguate) before relying on that assumption.

## 1.5.0
- Training Log: defaults to "Running" when available; the 1-year view now
  shows the trailing 12 *complete* calendar months (e.g. on any day in
  July, that's July last year through June this year) instead of a rolling
  365-day window, so it always lines up with clean month boundaries.
  Verified the month-boundary math directly, including the January/December
  year-rollover edge cases.
- Added a distance chart to the Training Log: daily bars for the 7-day/
  4-week views and month drill-down, monthly bars for the 1-year overview.
  Hand-rolled SVG, same approach as the pace chart on route pages.
- New database export/import on the Import & Sync page - useful for doing
  the initial bulk import and route matching on a faster machine, then
  moving the populated database to wherever you actually run the app.
  Export uses SQLite's own backup API (not a raw file copy) to avoid
  catching the database mid-write; import validates the uploaded file is
  actually a SQLite database with the expected tables before replacing
  anything, and re-runs schema migrations afterward in case the imported
  database is from an older app version. The confirmation dialog before
  replacing your data is wired through an external script rather than an
  inline handler, since inline JS/handlers can be silently blocked under
  Home Assistant's ingress - already learned that lesson with the mobile
  layout fix in 1.2.2, applied it here proactively this time.

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
