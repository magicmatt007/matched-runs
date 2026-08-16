# Changelog

## 1.17.7
- Fixed two problems specific to importing a Garmin "Export Your Data"
  account archive (1.17.6 made the import itself work; these are about
  the quality of what it imports): activities showed up named after
  their raw uploaded filename/path instead of anything meaningful, and
  had no "View on Garmin" link at all.
  - Root cause for both: a raw .fit file has no free-text title field,
    and the number embedded in its own filename is NOT the real Garmin
    activity ID (confirmed directly - that number 404s against Garmin's
    own API, even though it looks just as plausible as a real one, the
    same trap this app already avoided for Strava's export filenames).
  - Fix: Garmin's export also includes
    DI-Connect-Fitness/*_summarizedActivities.json - a full dump of every
    activity Garmin Connect knows about, each with its real name and real
    numeric activity ID. There's no filename/ID shared with the raw .fit
    file to join on directly, so activities are matched by start time
    instead (within a tolerance, since the two timestamps aren't always
    identical to the second even for a genuine match - confirmed
    directly, one real pair was 15s apart), cross-checked by distance to
    guard against a wrong match. New `garmin_activity_id` column
    (mirroring the existing `strava_activity_id`) stores the recovered
    ID for the "View on Garmin" link; matching also backfills the name.
  - Also applies retroactively: uploading just the summarizedActivities
    JSON files (no need to re-upload the raw .fit files) backfills name
    and link onto activities already imported - useful both for an
    export re-download and for fixing the activities 1.17.6 already
    imported before this existed.
  - Fit-file activities with no match at all (e.g. no
    summarizedActivities.json uploaded) now at least default to their
    sport type ("Running", "Hiking", ...) instead of the raw filename -
    still generic, but meaningfully better than before.
  - Verified against a real Garmin export: matched 1739 of 1744
    (99.7%) already-imported file-based activities to their real name
    and a Garmin activity ID confirmed (via a direct API call) to
    resolve to that exact activity.

## 1.17.6
- Fixed bulk-importing a full Garmin "Export Your Data" account archive,
  which previously failed completely - every file came back "unsupported
  file type" (0 imported). Garmin's export wraps each raw uploaded file
  in DI-Connect-Uploaded-Files inside its own individual .zip rather than
  a bare .fit/.gpx/.tcx (unlike the single-activity "Export to GPX"
  download this app was built against) - uploaded zips are now unzipped
  and their contents imported, recursively (so a zip-of-zips works too).
  Three follow-on issues, all found and fixed against a real ~240MB
  export:
  - The same underlying raw file can legitimately appear in more than one
    of Garmin's export zips - previously crashed the entire import
    partway through with a database UNIQUE constraint error once two
    same-named files landed in one batch, since new activities were only
    written to the database once, at the very end (this session runs
    with autoflush off) - so an earlier duplicate in the same batch
    wasn't yet visible to the "does this already exist?" check. Now
    flushed immediately so a same-batch repeat is correctly recognized
    and merged instead.
  - The import's progress bar tracked one increment per *uploaded* file -
    fine for individual .fit/.gpx/.tcx files, but Garmin's account export
    is a handful of zips each containing thousands of individual
    activity files, so the bar sat "stuck" at or near 100% for most of
    the import while a single worker ground through one huge zip alone.
    Zips are now expanded into their individual files up front, before
    any worker starts, so progress reflects real per-file counts and the
    parse pool distributes a big zip's contents across all workers
    instead of serializing on one.
  - Garmin's raw upload archive also carries plain monitoring/wellness
    .fit snapshots (heart rate, steps, etc.) that were never a recorded
    activity, alongside real workouts - tens of thousands of them in
    practice. These parsed "successfully" to a completely empty result
    (no GPS points, no duration, no distance) and were being imported as
    bogus zero-distance activities. Now recognized and skipped as having
    "no usable activity data", reported as its own count separate from
    "unsupported file type" so a bulk import's summary stays meaningful
    at this scale. A genuine indoor activity (real duration/distance, no
    GPS) is unaffected - only files with literally nothing usable are
    caught by this.
  - Verified end to end against a real Garmin account export (182
    top-level items expanding to ~50,000 individual files): completes
    without error, progress advances steadily instead of stalling, and
    the ~44,000 monitoring-snapshot files are correctly skipped rather
    than imported - recovering 1944 of an originally-1952-activity
    database (the small gap being pre-existing edge cases, not files
    this import touched).

## 1.17.5
- Replaced the hardcoded "Merge legacy types" action (which only knew two
  fixed pairs: Hiking/Walking and Kayaking/Rowing) with a fully
  customizable "Merge activity types" form - checkboxes for every type
  actually present in your data (with its activity count), merged into
  any destination type you type or pick (existing or new). The date
  cutoff is still available but now optional, via its own checkbox.
  Also adds a clear result panel after submitting ("4 activities
  relabeled from Training, Other to Cross training" / "No activities
  matched X - nothing was changed") - the old flow gave no feedback
  specific to the merge at all.
  - Verified against the real running app: the checkbox/count list
    renders correctly from real data, HTML5 validation blocks an empty
    destination, the cutoff-date checkbox correctly enables/disables the
    date field, and both the success and no-op feedback panels render
    correctly. The route's filtering/redirect logic was exercised via a
    harmless self-merge (a type merged into itself, which correctly
    no-ops) rather than a real merge on live data.

## 1.17.4
- The activity detail page's Elevation, Pace/Speed, and Heart Rate charts
  can now switch their x-axis between distance and elapsed time via a
  "X-axis: Distance / Time" toggle above them - useful for spotting where
  in the run/ride something happened by clock time rather than by
  position along the route. All three charts switch together, using the
  same elapsed_s each chart point already carries (real per-point timing,
  or the existing even-pace estimate for older imports) - no server
  changes needed. The choice is remembered (localStorage) across
  activities, and the "Time" option is hidden entirely for the rare
  activity with no timing data at all to plot.
  - Verified against the real running app: toggling re-renders all three
    charts with correctly formatted time-axis ticks/tooltips (h:mm:ss),
    hover/map-highlight still works, and the choice persists across a
    page reload.

## 1.17.3
- "Recompute matches" now runs as a background job with visible progress,
  instead of blocking the browser on a plain form submission with zero
  feedback until it finished - the O(n^2) full comparison this triggers
  can genuinely take a while for a large collection, exactly as
  reported. Matches the same pattern already used for import, Garmin
  sync, and the rename-by-city job: click the button, get redirected
  immediately, and a progress panel appears showing how many comparisons
  are done, an ETA, and a final "Recompute finished" message - reusing
  rebuild_groups' own existing progress-callback support (already used
  internally for the same purpose during a large import) rather than
  needing anything new there.
  - Verified through the real running app that the POST request itself
    returns near-instantly rather than blocking on the actual recompute,
    that the job reaches "done" with the correct comparison count for a
    real dataset, and that triggering it again while one is already
    running is correctly rejected rather than starting a second one
    concurrently. Also tested the progress display's own logic directly
    against every state (in progress with a known total, in progress
    before the total is known yet, finished, and failed).

## 1.17.2
- Activity tables (main activity list, a matched route's page, and the
  training log) no longer show a redundant activity type inside the name
  itself when it's already shown in its own column (or, on the training
  log, already implied by the page's own type filter) - e.g. "Antibes
  Open Water Swimming" now displays as just "Antibes". This is purely a
  display change - the stored name is completely untouched, still shown
  in full on the activity detail page, and still available as a hover
  tooltip over the shortened name in the table.
  - Only strips an exact trailing match (name ending with " " + the
    activity type), not "the type appears anywhere in the name" - a name
    like "Urdorf Running Club Meetup" is correctly left alone rather than
    losing meaningful context.
  - Falls back to showing the name in full if stripping would leave
    nothing - e.g. an activity whose name is only the type itself, with
    no location prefix at all.
  - Also added a general truncation safety net on the name column (with
    the full name still reachable via the same hover tooltip), narrower
    on mobile, so an unusually long name that doesn't match the
    redundant-suffix pattern can't blow up the table width either.
  - Verified against the exact reported examples ("Antibes Open Water
    Swimming", "Z\u00fcrich Open Water Swimming") through the real running
    app, across all three affected pages, plus the deliberately tricky
    edge cases (type mentioned mid-name but not as a suffix, a name that
    is only the type, case mismatches) directly against the actual model
    code.

## 1.17.1
- Fixed values wrapping awkwardly inside the 1.17.0 layout on narrower
  phones ("11.21 km" splitting off "km" onto its own line, "+730 m /
  -732 m" breaking mid-value) - a direct side effect of that change:
  forcing three equal columns to stop Pace from stretching also made
  each card narrower than the font size assumed.
  - Reduced the Distance/Duration/Pace font size and card padding to
    give the longest of the three (the pace value, with its "/km" suffix)
    comfortable room at typical phone widths - sized with a deliberately
    safe margin rather than a tight fit, since this is the second round
    of adjustment on this exact layout and erring smaller is preferable
    to still wrapping.
  - Shortened the elevation card to show its unit once instead of twice
    ("+730 / -732 m" instead of "+730 m / -732 m") - a few characters
    shorter and just as clear.
  - Verified both against the exact real numbers from the reported
    screenshot (Leukerbad Hiking: 11.21 km, 730m/732m elevation, etc.)
    through the real running app.
  - One honest limitation: this environment can't render actual CSS to
    confirm pixel-perfect text wrapping the way font-fit was verified
    computationally for the SVG chart labels earlier - the sizing here is
    a deliberately conservative estimate based on character count and
    the page's real padding values, not a rendered screenshot. If it's
    still wrapping (or now looks too small) on your phone specifically,
    that's useful to know.

## 1.17.0
- Reworked the top of the activity detail page for mobile, based on a
  real screenshot showing several space/layout issues:
  - **Distance/Duration/Pace** are now a true 3-column grid instead of a
    wrapping flex row - Pace was stretching to full width and dropping
    to its own line on narrow phones, since it didn't fit alongside the
    other two at their minimum width.
  - **Elevation gain/loss** and **avg/max heart rate** are now each a
    single combined card ("+120 m / -115 m", "152 / 178 bpm") instead of
    two separate cards apiece - roughly halves the height of that
    section. Falls back to showing just one value cleanly (no stray "/"
    or "avg/max" label) when only one side of the pair is actually
    present.
  - **"No matches found yet for this route"** moved from an isolated
    line between the two stat sections down to right by the map, where
    the matching it's describing actually happens.
  - **"Delete" moved out of the top action row**, away from "Next" -
    those being adjacent meant a common navigation tap and an
    irreversible destructive action sat right next to each other. Now
    lives in its own row near the map, at the bottom of the page.
  - Verified all of this against three real, distinct data shapes (full
    stats with a matched route, single-sided elevation/HR with no match
    yet, and no GPS track at all) through the actual running app, not
    just one "happy path" render.

## 1.16.9
- Simplified "via garmin ... View on Garmin Connect ↗" on the activity
  detail page down to just "via garmin ↗", with the source name itself
  as the link - removes the redundant second phrase without losing the
  ↗ cue that it opens elsewhere. Activities with no external page to link
  to (e.g. hand-named GPX files) are unaffected, still showing the plain
  source name as before.

## 1.16.8
- Fixed horizontally scrolling the training log's activity table (on
  narrow screens, where it scrolls sideways instead of squeezing the
  page) also triggering page swipe-navigation at the same time - the
  same underlying conflict as the map and the activity page's charts,
  just on an element that hadn't been accounted for.
  - Rather than add "the responsive table" as a fourth specific special
    case, generalized the exclusion: it now checks whether the touch
    started on an element (or one of its ancestors) that's genuinely
    horizontally scrollable at that moment, not a fixed list of class
    names. A future horizontally-scrollable element added anywhere in the
    app should be excluded automatically, rather than needing its own
    separate bug report the way this one did.
  - Verified directly: a touch landing on a table cell whose ancestor
    wrapper is the one that actually scrolls is correctly excluded (the
    realistic case, not just the wrapper div itself); a wrapper that
    merely has the same class but isn't actually overflowing (e.g. the
    table fits fine on a wide screen) is correctly NOT excluded, since
    only genuine scrollability matters, not the class name alone; and the
    map/chart exclusions and the previous fix's script-loading-order
    behavior are both unaffected by this change.

## 1.16.7
- **Fixed swipe navigation not working at all, on any page, since it was
  introduced in 1.16.5.** The 1.16.6 fix (the SVG exclusion being too
  broad) was real, but not the actual reason it never worked in the
  first place - it was papering over a symptom of a more fundamental
  bug underneath it. Root cause: the script is loaded from `<head>`,
  which runs *before* the page's own content - including the very
  Previous/Next links it looks for - exists in the DOM at all. It
  searched for those links immediately at load time instead of waiting
  for the page to finish loading, always found nothing, and silently did
  nothing, on every page, regardless of which page or whether the links
  were really there further down.
  - This should have been caught the first time - `local-time.js` already
    handles the exact same "loaded from head, needs the DOM to exist
    first" situation correctly, and this script should have followed
    that same, already-established pattern from the start rather than
    needing a second report to find it.
  - The previous testing gap: earlier verification tested the gesture-
    detection math in isolation, against a mock page that was always
    already fully loaded - which never exercises the actual failure mode
    here, where script-tag position in the real HTML determines whether
    anything below it exists yet. This time, verified using a real HTML
    document parsed in the correct order (via jsdom) - confirmed the
    previous version's listeners were never even registered in this
    realistic setup, confirmed the fixed version's are, and confirmed the
    same test correctly fails against the old code specifically (not just
    passing regardless of what it's given).

## 1.16.6
- **Fixed swipe navigation (1.16.5) not working on the training log
  page.** Root cause: the exclusion rule meant to avoid conflicting with
  the activity page's interactive charts was written as "skip any SVG,"
  but the training log has its own distance chart that's also drawn as
  an SVG - despite having no touch-drag behavior of its own to actually
  conflict with. Since that chart is large and sits right where someone
  would naturally try to swipe, this silently blocked the gesture there
  entirely, with no error or feedback of any kind.
  - Replaced the blanket "any SVG" exclusion with a precise marker
    (`.touch-interactive`) applied only to the specific charts that
    really do have their own touch handling (elevation/pace/heart rate,
    on the activity detail page). Verified directly that a plain SVG
    chart without this marker is no longer excluded, while the map and
    the genuinely interactive charts still are - confirmed against the
    real running app that the marker appears exactly where it should
    (the activity page's charts) and nowhere it shouldn't (the training
    log's own chart).

## 1.16.5
- Swipe left/right on touchscreens now works as an alternative to tapping
  the Previous/Next buttons, on both the activity detail page and the
  training log (in both its month-drill-down and period-based
  navigation modes). It's not a separate mechanism - the swipe just
  triggers whichever link the button itself already points to, so
  there's nothing new to keep in sync if that navigation logic ever
  changes.
  - Deliberately ignores swipes that start on the map or inside one of
    the elevation/pace/heart-rate charts, since both already use
    touch-drag themselves (map panning, chart hover-to-explore) and would
    otherwise conflict with a page-level swipe gesture meant for
    navigation.
  - Doesn't interfere with normal scrolling - it only ever looks at where
    a touch started and ended, never blocks the browser's own default
    handling of the gesture while it's happening.
  - Verified the actual gesture-detection math directly (not just that
    the code runs): a clear horizontal swipe in either direction
    triggers the right navigation, small/accidental movements and normal
    vertical scrolling are correctly ignored, a diagonal gesture with too
    much vertical component doesn't falsely trigger, a swipe with only
    slight vertical drift still correctly counts, multi-touch gestures
    (e.g. pinch-zoom) are ignored entirely, and swipes starting on the
    map or inside a chart are correctly excluded. Also confirmed against
    the real running app that the necessary markup is actually present on
    both real pages, in every navigation mode log.html has.

## 1.16.4
- The activity filters (on both the main activity list and a matched
  route's page) now collapse behind a "Filters" toggle on mobile-width
  screens, instead of always taking up the same space as on desktop.
  Desktop is unaffected - filters stay expanded there as before, since
  space isn't the problem there.
  - Any currently-active filters now show as individual chips (e.g.
    `Name: "Urdorf"`, `Distance: 10-20 km`) rather than just a plain
    count - visible even while the panel itself is collapsed, so it's
    never ambiguous what's actually narrowing the list. Each chip has its
    own "x" to remove just that one filter without opening the full
    panel.
  - Both pages now share one filter panel implementation instead of two
    separately-maintained copies of the same form.
  - Verified end-to-end against a real running instance of the app (not
    just template rendering in isolation) - seeded an actual test
    database, made real HTTP requests through a real FastAPI test client,
    and confirmed: chips render with correctly-formatted labels (including
    pace as m:ss, not raw decimal minutes), the group page correctly has
    no "Type" filter chip/field (routes are already one type), and -
    the specific thing that mattered most here - actually followed a
    chip's own "x" link and confirmed only that one filter cleared while
    the other active filters stayed exactly as they were.

## 1.16.3
- Activity dates and times now display in the viewer's actual local time
  instead of raw UTC (all sources - GPX, FIT, TCX, Garmin, Strava -
  consistently store timestamps as UTC, which was previously shown
  as-is with no conversion at all). Converted client-side in the browser
  rather than on the server, since the server can't reliably know what
  timezone the viewer is actually in - a self-hosted app can easily be
  viewed from a different timezone than the server itself.
  - Falls back to showing the UTC time if JavaScript is unavailable,
    rather than showing nothing.
  - Verified the actual conversion math (not just that code runs) against
    several real timezones, including the trickiest part - dates that
    roll over to the next (or previous) calendar day once shifted out of
    UTC - and confirmed DST transitions are handled correctly using the
    browser's own timezone database rather than a fixed offset.
  - This also fixed the group page's pace-over-time chart tooltips, which
    had the same issue via a different mechanism (a pre-formatted date
    string embedded in the chart's data rather than a template-rendered
    value) - now uses a shared conversion helper instead of duplicating
    the logic.

## 1.16.2
- Made the ingress-limit warnings explicit about the actual port number
  (8000, not Home Assistant's own 8123) rather than just saying "direct
  port" - prompted by the same local-IP-but-wrong-port mix-up happening
  twice now. Using the local network IP address alone doesn't bypass
  ingress; Home Assistant's own port (8123) still routes through it
  regardless of whether the address is local or public - it's
  specifically the port number that matters. Updated this in both
  warnings (database import and bulk activity import) and the client-side
  pre-flight alert.

## 1.16.1
- Added guidance to the bulk activity import section about upload size
  limits imposed by whatever's in front of this app (not by the app
  itself) - previously only the database import had this kind of warning.
  Prompted by a real report of a large folder upload failing with a
  Cloudflare "413 Payload Too Large" error after several minutes, when
  accessed through a Cloudflare Tunnel: confirmed directly against
  Cloudflare's own documentation that this is a hard, plan-dependent limit
  (100MB on Free/Pro, 200MB on Business) entirely separate from Home
  Assistant's own ~16MB ingress limit already documented here - both can
  independently reject a large import before it ever reaches this app.
  The fix in both cases is the same: use the direct local network URL for
  large imports, which bypasses both proxy layers at once.

## 1.16.0
- **Fixed a real duplicate-activity bug**: re-uploading a file (e.g. to
  pick up the 1.15.8 TCX distance fix) created a second activity instead
  of updating the existing one, if the file was uploaded outside its
  original bulk-export folder structure. Root cause: file-based
  external_id stored the full uploaded path (e.g.
  "export_98765/activities/123.tcx.gz"), which a standalone re-upload of
  the same file doesn't have ("123.tcx.gz") - so the "does this activity
  already exist" check never matched, and the same-source duplicate check
  doesn't apply within one source either.
  - New uploads now store external_id as just the filename, not the full
    path.
  - Existing activities get this normalized automatically on next
    startup, handled defensively: if two different activities would
    collide on the same basename (very unlikely, but the database has a
    uniqueness constraint on this), the normalization is skipped for that
    one with a warning logged, rather than crashing the whole migration.
  - Verified against a full reproduction of the actual reported sequence:
    a pre-fix database with the old path-prefixed ID, through the
    migration, through a simulated re-upload - confirmed it now correctly
    updates the existing row instead of creating a duplicate.
- Added the ability to delete a single activity (no such feature existed
  before this), needed to clean up activities already duplicated by the
  bug above. A "Delete" button now appears on the activity detail page
  with a confirmation prompt. Deleting an activity that was the last one
  in its matched route group also removes the now-empty group; deleting
  one of several leaves the group and its other activities untouched.
  Verified by executing the actual route logic (not just reading it)
  across all of: last-activity-in-group, one-of-several, no group at all,
  and a nonexistent activity ID.
- This module's own earlier miss (the 1.15.9 crash from incomplete
  testing) changed how this was verified: every piece of this fix -
  the migration, the collision handling, the full bug reproduction, and
  the new delete route across all its branches - was actually executed
  against realistic data before shipping, not just read back for a second
  time.

## 1.15.9
- **Fixed a crash introduced in 1.15.8**: "Import failed:
  app.models.Activity() got multiple values for keyword argument
  'distance_m'". Cause: 1.15.8 added distance_m to a dict that gets
  spread into Activity(...) with **, but distance_m was already being
  passed to that same call explicitly - Python correctly rejected the
  duplicate. This only affected saving a brand-new activity; re-uploading
  an already-imported one to correct its distance (the actual point of
  that change) was unaffected.
  - This should have been caught before shipping 1.15.8. My testing at
    the time only inspected two of the three code paths that use the
    shared field dictionary, not the one that actually broke. This time,
    verified by actually executing all three paths against the real
    function code (not just reading it), including reproducing the exact
    reported error against the old structure first to confirm the test
    would have caught it, then confirming the fix resolves all three.

## 1.15.8
- Fixed a TCX activity showing 0.01 km instead of its real distance
  (confirmed on the actual reported file: an 82km ride). Root cause,
  found by inspecting the file directly: it had exactly one
  `DistanceMeters` value in the whole file - a Lap-level summary reading
  9.9 meters, with zero per-trackpoint distance anywhere, even though all
  4045 trackpoints had valid GPS positions throughout. That summary value
  is simply wrong (a device/export bug, not user error) - other TCX files
  with a genuinely correct Lap-level summary and no per-trackpoint
  distance still exist (this remains the fallback for indoor activities
  with no GPS at all), so this isn't a hardcoded "ignore the summary"
  change.
  - Now computes GPS-derived distance (summing consecutive recorded
    positions - the same haversine math already used for route matching)
    as a fallback when there's no per-trackpoint distance, before ever
    falling back to a Lap-level summary. Confirmed directly against the
    actual reported file: GPS-derived distance comes out to 82.02 km,
    matching what Strava itself displays (82.09 km) far more closely than
    the broken 9.9m the file's own summary claimed.
  - distance_m is now also included when backfilling an already-imported
    activity on re-upload (previously only metadata like name/elevation/
    heart rate got backfilled, not distance itself) - specifically so an
    activity already imported with the wrong distance can be corrected by
    simply re-uploading the same file, rather than needing to be deleted
    and re-imported from scratch.
  - GPX was already safe from this class of bug (it computes its own
    GPS-derived length rather than trusting an embedded summary field at
    all). Left FIT parsing unchanged - no evidence of the same issue
    there, and its device-reported total_distance is generally reliable
    when present, so this was scoped to the actual confirmed case rather
    than applied speculatively.

## 1.15.7
- **Fixed a real bug from 1.15.6**: the "View on Strava" links added for
  Strava bulk-exported GPX/FIT/TCX activities could point to a completely
  unrelated activity belonging to a different person entirely. The
  previous version treated a purely-numeric exported filename (e.g.
  "971607640.gpx") as if it were the real Strava activity ID - confirmed
  via direct testing that this assumption was simply wrong, contradicting
  the (misread) research behind it. Removed that filename-based guess
  entirely.
  - The correct, verified-working source is the "Activity ID" column in
    Strava's own activities.csv, which is now parsed and backfilled onto
    each matching activity as a dedicated field (not reusing external_id,
    which stays as the filename for other purposes like duplicate
    detection).
  - For activities that already picked up an incorrect link under 1.15.6:
    the link will simply not show at all now (safe default) until you
    re-upload activities.csv - no need to re-upload the GPX/FIT/TCX files
    themselves, since existing activities get backfilled the same way
    names already do.
  - Verified directly against the actual property code (not a rewritten
    stand-in) that the dangerous filename-based path is completely gone,
    and that the correct field-based path produces the right link even
    when the misleading filename number is a totally different value from
    the real activity ID - reproducing the exact scenario reported.

## 1.15.6
- Added the same external Garmin Connect/Strava links to the route page's
  activities table (which already had a Source column), not just the
  activity detail page.
- These links now also work for activities imported via Strava's bulk
  export, not just live-synced ones - previously only source=="strava"
  (live sync) got a link. Strava names each raw exported file after its
  real activity ID (e.g. "971607640.gpx"), which is both already stored
  as this app's external_id for file imports and a more reliable source
  for the ID than it first seems: Strava's own community forum has
  documented real cases of the "Activity ID" column in activities.csv
  itself being mismatched to a completely different activity, which
  would make a link built from that column actively wrong rather than
  merely unavailable. Only a purely-numeric filename is treated as a
  Strava ID, so a normally-named GPX/FIT/TCX file correctly gets no link
  rather than a guess.
- Refactored both pages to share one property (external_activity_url)
  instead of duplicating the same source-based logic in two templates.
  Verified directly against the actual code in models.py (not a rewritten
  equivalent) across every source/filename combination: live Garmin,
  live Strava, bulk-export GPX/FIT/TCX (including gzipped and with a
  folder path prefix), a hand-named file that's correctly excluded, and
  missing/empty IDs that would otherwise produce a broken link.

## 1.15.5
- Activity detail pages now link to the original activity on Garmin
  Connect or Strava (opens in a new tab), right next to where the source
  is already shown. Only appears for activities that actually came from
  those two sources - file-based imports (GPX/FIT/TCX) have no external
  page to link to, so nothing shows there. Verified the URL formats
  against each service's own documentation rather than assuming from
  memory (Strava's own help center confirms the exact pattern), confirmed
  the right link appears for the right source and never the wrong one,
  and confirmed a defensive fallback for the unexpected case of a Garmin/
  Strava activity somehow missing its external ID, which would otherwise
  produce a broken link.

## 1.15.4
- The "Pace over time" chart on a matched route's page now draws a
  smoothed trend line (a centered moving average across nearby runs)
  instead of connecting every single run's exact pace directly - run-to-
  run pace naturally varies a lot with conditions, effort, and terrain,
  which was making it hard to see whether the underlying trend was
  actually improving. The individual dots still show each run's real,
  unsmoothed pace (still hoverable and clickable through to that
  activity) - only the connecting line changed. Verified the smoothing
  math against hand-calculated values, confirmed it genuinely reduces
  variance on a noisy alternating-pace test signal rather than just
  passing the data through unchanged, and confirmed the few-activities
  edge cases (a route matched from only 1-2 runs) don't crash.

## 1.15.3
- Training Log now defaults to "Last year" instead of "Last 7 days".
- Activity detail chart order changed to Elevation, Pace, Heart Rate.
- Fixed the heart rate chart's y-axis labels being clipped ("158 bpm"
  showing as unreadable) - same root cause and same fix as the pace
  chart's clipping fixed last time: the axis labels included the full
  " bpm" unit, needing more room than was reserved for them. Applied the
  same fix to the elevation chart too, even though it wasn't reported as
  broken, since the same reasoning applies and the chart title already
  states the unit either way. Confirmed the exact clipping amount
  ("158 bpm" needing 50px in only 38px of space) before fixing it, and
  confirmed the fix leaves 16-24px of margin for realistic heart rate
  values. Average-line labels and the hover tooltip keep the full unit,
  since only the axis itself was short on room.

## 1.15.2
- Fixed a brief pause (e.g. stopping for a photo at a summit) producing a
  wildly inflated pace value that dominated the whole chart's y-axis
  scale, flattening every other segment into a nearly straight line -
  seen on a cycling activity where a rest stop at the top of a climb
  showed as several hours-per-km. Real elapsed time over essentially zero
  real movement (whatever tiny distance shows up there is just GPS jitter
  while stationary) produces a pace calculation that's technically
  correct but not meaningful. Now filtered out using each activity's own
  median pace as the reference point (robust to the outlier itself,
  unlike a plain average would be) rather than a fixed cutoff, since
  normal pace varies enormously between a run and a bike ride. Verified
  against a reconstruction of the actual reported scenario (climb, pause,
  descent) - the pause is correctly dropped while both the slow climb and
  fast descent pace values are preserved untouched.
- Cycling activities now show "Speed (km/h)" instead of "Pace (min/km)",
  matching how cyclists actually think about their performance (and how
  Strava/Garmin Connect present it too) - detected via a keyword match
  (cycl/bik) against the activity type, robust to however a specific
  source phrases it (Road Biking, Mountain Biking, Gravel Cycling,
  E-Biking, etc.), rather than requiring an exact string match. Faster
  now correctly renders higher on the chart either way - pace and speed
  have opposite numeric relationships to "faster", so the y-axis
  orientation flips between the two, verified directly for both.

## 1.15.1
- Activities imported before the pace chart was added no longer show "not
  enough data" - pace is now approximated from the activity's total
  duration (assuming roughly even sampling across the route) when real
  per-point timestamps aren't available, rather than requiring a
  re-import. A small note appears under the chart in this case, since it's
  a reasonable approximation, not as precise as activities with exact
  per-point timing (it can't reflect pauses or variable GPS sampling
  rates). Verified the synthesized pace lands on the expected value for a
  known scenario, and that this fallback never fabricates data when
  there's genuinely nothing to estimate from either.
- Fixed the pace chart's y-axis labels getting clipped ("5:51 /km" showing
  as just "51 /km"). Root cause: the axis labels were using the same
  format as the average-line label and hover tooltip (which include the
  full " /km" unit and have more room to spare), but the axis itself only
  had enough space reserved for elevation/heart rate's much shorter plain
  numbers. Confirmed this exact math against the reported screenshot
  before fixing it (57.6px of text trying to fit in 38px of space,
  clipping ~19.6px off the left - matching what was actually shown).
  Axis labels now use a shorter unit-free format (the chart's title
  already says "Pace (min/km)"), with extra margin reserved to
  comfortably fit slower double-digit-minute paces too.

## 1.15.0
- Added a pace chart to the activity detail page, alongside elevation and
  heart rate - same interactivity (distance units, average line, hover
  for exact values, chart-to-map linking). Faster pace renders higher on
  the chart, matching the convention the route page's own pace-over-time
  chart already uses.
  - Pace isn't recorded directly the way elevation/heart rate are - it's
    derived from distance and elapsed time between points, which required
    storing per-point timestamps for the first time (a new column; only
    file-based imports and Garmin live sync populate it, same scope as
    elevation/heart rate). Deriving pace between consecutive *downsampled*
    chart points (not raw GPS points) rather than adding a separate
    smoothing step - naturally avoids the GPS jitter that would make a
    true point-to-point pace unreadable, for free.
  - Verified the pace math against a known scenario (200m/60s segments ->
    confirmed ~5:00/km), confirmed a paused segment (zero distance, time
    still elapsing) correctly gives "no pace" instead of an infinite/
    garbage value, and confirmed activities with only some of elevation/
    heart rate/timing data still chart whatever they do have. Also
    verified the inverted y-axis directly (faster pace produces a smaller
    y-coordinate, i.e. renders higher) rather than assuming the sign was
    right.

## 1.14.0
- Elevation and heart rate charts on the activity detail page are now
  properly interactive, all four requested improvements:
  - **X-axis units**: now labeled with actual distance along the route
    (km), not just point order.
  - **Average line**: a dashed reference line with its value labeled.
  - **Hover for exact values**: moving the mouse (or a finger, on
    touchscreens) over either chart shows the precise value and distance
    at that point.
  - **Chart-to-map linking**: hovering a point on either chart now shows
    exactly where on the map that elevation/heart rate occurred, via a
    marker that follows your cursor across the chart.
  - This required a real backend change: elevation, heart rate, GPS
    position, and cumulative distance are now built into one combined,
    aligned series per activity (previously elevation and heart rate were
    two independent, unlinked arrays) - that's what makes it possible to
    know which map location corresponds to which point on either chart.
    Cumulative distance is computed via the same haversine function
    already used for route matching.
  - Verified the distance computation, the downsampling (always keeping
    the true endpoint), the nearest-point hover lookup at exact matches,
    midpoints, and out-of-range positions, and the actual hover-to-map
    linking end-to-end (including that leaving the chart correctly hides
    the map marker again) - all directly executed, not just read through.

## 1.13.1
- Fixed the database import silently hanging forever (stuck at a tiny
  percentage, no error, no feedback) when attempted through Home
  Assistant's ingress with a file over the ~16MB limit - the in-app
  warning about this was already there, but nothing stopped you from
  starting the doomed upload anyway. Now checks the selected file's size
  against the ingress limit *before* starting the upload, and blocks it
  immediately with a clear explanation instead of leaving you watching a
  progress bar that was never going to finish. Verified across all the
  boundary cases (ingress vs. direct port, just under vs. just over the
  threshold).

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
