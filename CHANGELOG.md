# Changelog

## 1.13.0
- The 1.12.2 timing logs paid off: for a real 2020-file Strava import,
  parsing was 95.7% of the entire 977-second total (934.6s), while route
  matching (17.5s) and per-activity saving/dedup (21.8s) - the things
  originally suspected - turned out to barely matter at all. Both earlier
  hypotheses were wrong; the data made that unambiguous.
  - Tried a targeted fix first: the FIT parser calls a `get_value(name)`
    method up to 6 times per GPS point, each doing its own internal search
    through that record's fields. Rewrote it to build a lookup dict once
    per point instead. Verified functionally identical output against a
    stub matching the library's documented API - but a directional timing
    test of the access pattern itself came back inconclusive (the "faster"
    version was marginally slower in that isolated test), so this is
    shipped as a harmless, correctness-verified change, not a claimed
    performance fix.
  - The real fix: parsing is CPU-bound and every file is independent of
    every other, so bulk import now parses files across multiple worker
    processes (up to 8, or fewer on lower-core hardware like a Raspberry
    Pi) instead of one at a time on a single core. Saving to the database
    stays sequential (SQLite doesn't want concurrent writers, and it was
    only ~2% of total time anyway). Verified the actual parallel
    infrastructure for real - a genuine multi-process pool, real TCX
    parsing across process boundaries, gzip decompression inside worker
    processes, and graceful error handling for corrupt/unsupported files
    without crashing the pool - but the sandbox this was built in only has
    a single CPU core available, so the actual speedup on real multi-core
    hardware could not be measured here and needs a real test to confirm.
  - Timing logs from 1.12.2 are retained and extended to show the new
    parse/save split explicitly.

## 1.12.2
- Added detailed phase timing to the bulk import job's logs (per-file
  parsing time vs. per-file saving time, name backfill, commit, and the
  route-matching phase, each logged separately with a running progress
  log every 200 files) - in response to a report that a 2000-activity
  Strava import now takes noticeably longer than it seemed to before.
  Several real candidates exist (parsers now extract more per-point data,
  every new activity runs a cross-source duplicate check against the
  database, and a large import triggers the O(n^2) full route-matching
  rebuild), but rather than guess which one actually dominates - the same
  mistake made repeatedly during the database import investigation before
  real log data settled it - this adds the instrumentation first. Not a
  performance fix yet; the next step is re-running a comparably large
  import and reading the logs.

## 1.12.1
- Corrected an inaccurate claim from 1.12.0: Garmin live sync actually
  *can* populate the new elevation/heart rate charts - it already
  downloads and parses a full GPX file per activity (not just a summary
  API call), and that parser already extracts per-point elevation/heart
  rate. The data was available all along; it just wasn't being passed
  through to storage. Fixed the oversight, and confirmed via independent
  sources that Garmin Connect's GPX export does include barometric
  elevation and heart-rate sensor data (cadence and power genuinely aren't
  in GPX, which matches why cadence already came from Garmin's summary API
  field rather than the file).
  Strava sync remains genuinely unable to populate these charts without a
  bigger integration change - it only ever decodes a summary polyline
  (lat/lon only, no elevation/HR possible), and getting per-point data
  would mean calling Strava's separate activity-streams endpoint with an
  extra API call per activity, on an integration already de-prioritized in
  this app since Strava's API now requires a paid tier.

## 1.12.0
- Activity detail pages now show elevation and heart rate charts over the
  course of the activity, alongside the existing summary stats (gain/loss,
  avg/max). This required storing per-point elevation/heart-rate series
  for the first time - previously only summary totals were kept, the raw
  per-point readings were computed then discarded. Each is downsampled to
  at most 150 points for a lightweight chart even on a multi-thousand-point
  GPS track, and gracefully falls back to a "not enough data" message
  rather than showing an empty/broken chart when a source doesn't have
  this data.
  - Only file-based imports (GPX/FIT/TCX) populate these charts - Garmin/
    Strava live sync only exposes summary totals from the endpoints this
    app calls, not a per-point series. Existing already-imported activities
    won't show these charts until re-uploaded/re-synced (same as previous
    metadata-only fields).
  - Found and fixed a real per-point misalignment bug in the TCX parser
    while building this: heart rate readings were being collected into a
    flat list whenever found, independent of whether that trackpoint later
    turned out to have no GPS position and got skipped entirely - meaning
    the heart rate series could end up silently offset from the points/
    elevation arrays it needs to line up with. Restructured to build the
    per-point-aligned series at the same point a trackpoint is kept or
    discarded, and verified the fix with a synthetic file containing both
    a missing HR reading and a trackpoint with no position at all.
  - Caught a real startup-crashing bug (the same class as 1.10.1's) while
    building the route logic - a decorator ended up separated from the
    function it was meant to register by newly-inserted code in between.
    The `ast`-based route/decorator safety check added after 1.10.1 caught
    it immediately, before it ever left this session.

## 1.11.1
- Fixed the Training Log distance chart's y-axis not actually using the
  space freed up by shortening its labels (whole numbers, no "km") -
  the left margin reserved for those labels was untouched, so the plot
  area, and the room available for x-axis labels, never grew. Shrunk that
  margin to match the now-much-shorter labels.
- The 1-year view's x-axis (always exactly 12 short month labels) no
  longer skips any of them regardless of screen width - previously it
  used the same adaptive width-based logic as the daily views, which on a
  narrow phone was still dropping half the months even after the above
  fix. Since there are never more than 12 of these short labels, there's
  no good reason to ever hide any.

## 1.11.0
- Training Log's distance chart y-axis now shows whole numbers only, no
  "km" suffix cluttering every tick label (unit moved to the chart title
  instead: "Distance (km)").
- New "Rename all activities by city" button on Import & Sync, renaming
  every activity to "[City] [Activity Type]" (e.g. "Zurich Running") based
  on its GPS start point, via OpenStreetMap's free Nominatim geocoding
  service (same data source already used for the map tiles). Runs as a
  background job with the same progress-bar/ETA treatment as the other
  slow actions, since Nominatim's usage policy caps lookups at ~1/second -
  results are cached by location, so re-running this later (e.g. after a
  new import) only needs to look up genuinely new locations. Indoor
  activities with no GPS track are left untouched. Verified the caching,
  the location-field fallback chain (city → town → village → municipality
  → suburb → county), the 1-request/second rate limiting, and graceful
  handling of failed/unmatched lookups against a mocked geocoding service
  before wiring it up for real.
- Activity detail pages now show distance/duration/pace as prominent stat
  cards (matching the treatment elevation/heart rate/etc already had),
  instead of small dimmed text easy to miss. The date/type/source line and
  match-status line also switched from dimmed "muted" styling to normal
  text.
- Home Assistant install defaults changed: GARMIN_SYNC_INTERVAL_MINUTES
  60 (was 120), MATCH_DISTANCE_THRESHOLD_M 85 (was 50),
  MATCH_LENGTH_TOLERANCE unchanged at 0.15. These three are now mandatory
  in Home Assistant's configuration screen (no longer optional/hidden) with
  their actual default values shown, rather than silently falling back to
  a hardcoded value the Supervisor's config screen never surfaced. The
  same defaults apply to a standalone docker-compose install too, for
  consistency between the two deployment methods.

## 1.10.3
- Training Log's distance chart now rotates its x-axis date labels 90
  degrees instead of skipping most of them on narrow screens - rotated
  text only needs roughly its own height worth of horizontal space instead
  of its full text width, so far more labels fit before any need to be
  dropped. Verified the improvement directly: a 4-week (28-day) view on a
  typical phone width now shows ~36% of labels instead of ~21% before, and
  a 1-year view's 12 month labels now all fit even on small phones
  (previously only ~6 of 12 would fit).

## 1.10.2
- Fixed tiny, hard-to-read chart labels on mobile (Training Log's distance
  chart, and the pace-over-time chart on each route's page). Root cause:
  both charts used a fixed SVG coordinate system (800 units wide)
  regardless of actual screen size, and since the whole SVG scales down to
  fit its container, a fixed font-size shrinks right along with it - on a
  ~350px-wide phone, an "11-unit" label was rendering as roughly 5 real
  pixels. Both charts now size their coordinate system to the container's
  actual rendered width, so 1 SVG unit = 1 real screen pixel on any
  device, and bumped the base font sizes for better legibility even on
  desktop. Also made the number of x-axis date labels shown on the
  distance chart adapt to how much width is actually available, rather
  than a fixed count, so labels don't overlap on narrow screens - verified
  this stays comfortably spaced (46-58px per label) across desktop,
  tablet, and phone-width scenarios before shipping.

## 1.10.1
- Fixed a broken app startup introduced in 1.10.0: the `@app.get("/manage")`
  decorator ended up attached to the wrong function (`_group_activity_counts`,
  a private helper, not the actual page handler) after removing a line
  between them - FastAPI tried to register that helper as the route handler
  itself, and its `db: Session` parameter (no `Depends()`, since it was never
  meant to be a route) made FastAPI treat it as an invalid request field,
  crashing the whole app on startup before it could serve anything at all.
  `py_compile` (what every "PY OK" check in this project relies on) only
  verifies syntax - it doesn't execute module-level code, so it can't catch
  a decorator landing on the wrong function; that only shows up when
  something actually tries to import/run the module, which is exactly what
  happened here. Added a proper safeguard: a static check (via Python's
  `ast` module) that walks every `@app.<method>(...)` decorator in the file
  and confirms it's attached to a sensibly-named handler function, catching
  this entire class of bug without needing the full dependency stack
  installed. Confirmed clean with this check before shipping this fix, and
  will run it going forward.

## 1.10.0
- The Import & Sync page no longer shows "Matched routes" or "Unmatched
  activities" at all - good question from testing that surfaced this: it
  had no real reason to query or display activity data in the first
  place, since that's exactly what the Home page and route pages are for.
  This dates back to when Home was first split out from Import & Sync;
  the plan then was for a dedicated routes page, which never actually got
  built - these sections just stayed on Import & Sync ever since, adding
  unnecessary queries to a page that should just be about actions (upload,
  sync, export/import, cleanup tools).
  "Matched routes" now has its own page (linked from the header, next to
  Training Log). "Unmatched activities" isn't shown separately anywhere
  anymore - the Home page's "All Activities" table already covers exactly
  the same activities (marked "no match" in the Matched Route column), with
  sorting/filtering/pagination Import & Sync's old plain grid never had.

## 1.9.7
- Found and fixed a real, independent performance problem: navigating to
  the Import & Sync page was slow *on its own*, completely unrelated to
  the import feature (confirmed by testing it directly) - this validates
  the 1.9.6 theory about that page's render being slow, but as a
  standalone issue, not just a side effect of the redirect bug.
  Cause: computing each route group's activity count did
  `len(group.activities)` per group, which lazy-loads that group's full
  activity list with a separate SQL query *every time* - a classic N+1
  query problem. With enough route groups (years of history naturally
  accumulates many), this turned one page load into potentially hundreds
  of individual queries, which adds up fast on weaker hardware. The exact
  same pattern also existed in the "All Activities" page and the Training
  Log's activity table (up to ~100 extra queries per page load each) and
  the single-activity detail page (one extra query).
  Replaced all four with a single `GROUP BY` aggregate query computed once
  per page load, regardless of how many route groups exist. Also capped
  the "Unmatched activities" list on the Import & Sync page at 50 (it had
  no limit at all before, so years of one-off/indoor activities were being
  queried and rendered in full on every visit) with a link to the full,
  paginated "All Activities" page for the rest.

## 1.9.6
- The 1.9.5 extraction fix itself worked exactly as intended (confirmed
  against a second real log capture: 38.75s → 5.56s for the same ~118MB
  file - a ~7x improvement on actual Pi hardware), but a bigger issue was
  hiding behind it: the background job finished at 15:11:59 in that log,
  yet the next request the server saw was at 15:12:36 - a 37 second gap
  with nothing happening at all, well after the import itself was done.
  Cause: `XMLHttpRequest` automatically follows redirects, and the import
  endpoint returned one (to support the old plain-form-submission flow).
  That meant the XHR - used specifically to get real upload progress -
  was transparently waiting for the *entire* `/manage` page to finish
  rendering (querying every route group and activity) before `xhr.load`
  fired, adding a slow, completely untracked delay that has nothing to do
  with the import pipeline itself. This is very likely the actual
  remaining gap being reported, on top of whatever the 1.9.5 fix already
  improved.
  Fixed by having this endpoint return a minimal, fast acknowledgment
  instead of a redirect, so `xhr.load` fires immediately once the
  background job is spawned rather than waiting on an unrelated page
  render. Also fixed a related gap while in this code: `xhr.load` fires
  for HTTP error responses too (e.g. 409 "already running"), not just
  successes - the JS now checks the status code before assuming success
  and starting to poll for a job that never started.

## 1.9.5
- Found the real cause of the database import gap, thanks to the timing
  logs added in 1.9.4 and a real log capture from an actual Raspberry Pi
  3B import: 1.9.3's own fix was the problem. Extracting the file from the
  multipart body used `bytes.split()` across the whole ~118MB buffer, and
  on the Pi's weak CPU that alone took **38.75 seconds** - more than every
  other phase combined (receive 32.6s, write 2.8s, validate 1.1s, replace
  2.4s, migrate 0.03s) - and it wasn't tracked by any progress phase,
  which is exactly the invisible gap being reported.
  Rewrote the extraction to never scan the large file content at all: the
  headers are always small and near the very start (bounded search), and
  the closing boundary has a fully-known fixed length, so its position is
  *computed* from the end of the buffer via `bytes.endswith()` (which only
  compares the tail, not the whole buffer) instead of searched for.
  Benchmarked at 24-29x faster against the old approach on a 120MB
  synthetic body, and verified byte-for-byte identical output.
  Also folded the previously-separate "joining chunks" step (7.9s in the
  same log capture, itself untracked) directly into the receiving loop by
  using a growable bytearray instead of building a list and joining it
  afterward - removing that gap too, and avoiding an extra full-buffer
  copy in the process.
  Kept the phase-by-phase timing logs from 1.9.4 in place, since they're
  what actually made this diagnosable instead of another guess.

## 1.9.4
- 1.9.3 apparently didn't fix the database import progress gap either (it
  reportedly got *longer*) - rather than guess at the architecture a fifth
  time, this adds precise timing logs to every single phase of the import
  pipeline: request received, stream reading finished, chunk joining
  finished, multipart extraction finished, background job spawned,
  background job actually started, and each of writing/validating/
  replacing/migrating within it. This is a diagnostic release, not a
  claimed fix - the next step is running an import once more and reading
  off the container logs to see exactly where the real time is going,
  since continuing to reason about framework internals without that data
  hasn't worked.

## 1.9.3
- Actually fixed the database import progress gap this time (1.9.2's fix
  didn't help, as reported) - the real cause: FastAPI's `UploadFile =
  File(...)` parameter makes Starlette fully consume and parse the entire
  multipart request body as part of resolving that dependency, before the
  route function's own code runs at all. 1.9.2's "chunked read" was
  reading from a file Starlette had already fully received and buffered
  internally - so no amount of chunking inside the function body could
  ever make that phase visible, no matter how it was written. Confirmed
  this against a FastAPI maintainer discussion of the exact same issue
  before writing another fix blind a third time.
  The fix: stop declaring `UploadFile = File(...)` for this route entirely,
  and consume `request.stream()` directly instead, which does give real
  chunk-by-chunk access as bytes physically arrive over the network. Since
  this bypasses Starlette's automatic multipart parsing, the file's
  content now gets extracted with a small hand-written parser instead
  (intentionally not a general-purpose multipart parser - just enough for
  our own upload form's shape: one file field, nothing else). Verified
  this against a realistic browser-shaped multipart body byte-for-byte,
  including the tricky case of binary content containing embedded CRLF
  sequences, before wiring it into the actual route.

## 1.9.2
- Fixed the remaining gap in database import progress: after the browser
  finishes sending the file (which 1.9.1 made visible via XHR upload
  progress), the server still had to read the whole thing into memory via
  a single blocking `await db_file.read()` before the background job -
  and *that* was completely untracked, causing the progress bar to sit at
  100% for several seconds with no feedback before jumping to "done". Now
  reads the upload in 1MB chunks with progress reported after each one
  (new "receiving" phase, shown before "writing"), closing the gap between
  what the browser reports and what the server-side job tracks.

## 1.9.1
- Fixed the database import progress bar only appearing in the final
  second: the 1.9.0 implementation only tracked progress *after* the
  server had fully received the uploaded file - but for a large file, the
  upload itself (browser sending it, and the server receiving/spooling it)
  is usually most of the actual wait, and that part was completely
  invisible. Switched the upload to use XMLHttpRequest with real upload
  progress events instead of a plain form POST, so the progress bar now
  tracks the entire 10-15+ second upload from the start, then hands off to
  the existing server-side job polling for the post-upload validate/
  replace/migrate steps. Verified the progress percentage and ETA
  calculation against a simulated realistic upload (98MB over 12 seconds,
  sampled every 2 seconds) rather than just the instant-completion case
  tested in 1.9.0.

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
