# Matched Runs

A self-hosted "matched runs" tool: it groups your runs by route, the same
idea as Strava's paid "matched runs" feature, but free and running on your
own machine.

## How matching works

Each run's GPS track is resampled to 40 evenly-spaced points along its
length, then compared point-by-point against every other run (checked in
both directions, so an out-and-back loop run either way still matches).
Runs whose total distance is within 15% of each other and whose average
point deviation is under 50m are grouped together. Both thresholds are
configurable in `.env`.

## Getting your data in

Four independent ways to import runs — use any combination:

**1. Bulk import your whole history (recommended for existing data)**

From Garmin: Account Settings → Data Management → **Export Your Data**.
From Strava (this also covers pre-Garmin history, e.g. an old Polar
account that synced into Strava): Settings → **My Account** → "Download or
Delete Your Account" → **Request Your Archive**.

Either way, Garmin/Strava emails you a download link (usually within a few
hours to a day) with a zip of every activity, as `.gpx` or `.fit` files
(Strava sometimes gzips them as `.gpx.gz` / `.fit.gz` — that's handled
automatically). Unzip it, then use the app's "Upload entire folder" button
to import everything in one go. Files with no GPS data (indoor treadmill
runs, etc.) are skipped automatically.

**2. GPX upload, one at a time (fine for occasional new runs)**
Garmin Connect lets you export a single activity as GPX (Activity page →
gear icon → "Export to GPX"). Drop `.gpx` files on the app's upload form.

**3. Strava sync (optional, automatic)**
Since you already sync Garmin → Strava, this can pull runs straight from
Strava's API instead of manual exports:
1. Go to https://www.strava.com/settings/api and create an API application.
   Set "Authorization Callback Domain" to `localhost` (or your server's
   domain if not running locally).
2. Copy `.env.example` to `.env` and fill in `STRAVA_CLIENT_ID` and
   `STRAVA_CLIENT_SECRET`.
3. Start the app, click "Connect Strava", and authorize it.
4. Click "Sync from Strava" any time to pull new runs.

Note: Strava's API returns a *simplified* polyline for each activity
(`summary_polyline`), not the full-resolution track. This is precise enough
for route matching, but the map view will look slightly less detailed than
a raw GPX track.

**4. Automatic Garmin sync (unofficial, ongoing)**

Unlike the exports above (one-time history dumps), this checks your Garmin
account periodically and imports new activities automatically, so you don't
have to manually export/upload every run going forward.

There's no public Garmin API for personal/hobbyist use — Garmin's official
developer program is enterprise-only — so this works the same way tools like
GarminDB do: it logs into your Garmin Connect account directly. Be aware this
is unofficial and can break if Garmin changes their login flow (it has
happened before). Treat it as convenient-but-not-guaranteed, and keep the
manual GPX/FIT export path in mind as a fallback.

Setup:
1. Set `GARMIN_EMAIL` and `GARMIN_PASSWORD` in `.env`.
2. **If your Garmin account has MFA/2FA enabled** (recommended for security,
   but it means a background job can't complete the login on its own), run
   this once, interactively, before starting the app normally:
   ```bash
   docker compose run --rm -it matched-runs python garmin_login.py
   ```
   Follow the prompts (including entering your MFA code when asked). This
   saves a session that's reused automatically afterwards — you shouldn't
   need to do this again for about a year.
3. If your account does *not* have MFA enabled, you can skip step 2 —
   `GARMIN_EMAIL`/`GARMIN_PASSWORD` alone are enough.
4. Start the app normally. It checks Garmin for new activities every
   `GARMIN_SYNC_INTERVAL_MINUTES` (default 120), and there's also a "Sync
   from Garmin now" button on the homepage for an on-demand check.

If sync stops working after previously working fine, it's most likely Garmin
having changed something on their end — check
https://github.com/cyberjunky/python-garminconnect/issues for a fix/update
before assuming something's misconfigured.

## Running it

```bash
cp .env.example .env    # edit if using Strava sync
docker compose up --build -d
```

Then open http://localhost:8000

All data (SQLite DB) is stored in `./data`, so it persists across restarts.

## Notes / things you may want to tune

- `MATCH_DISTANCE_THRESHOLD_M` and `MATCH_LENGTH_TOLERANCE` in `.env`
  control how strict matching is. Loosen them if similar-but-not-identical
  routes (e.g. a route with a couple of small variations) aren't matching;
  tighten them if unrelated routes are getting grouped.
- The homepage has cleanup buttons for data that predates a given feature:
  **"Recompute matches"** re-runs route grouping from scratch;
  **"Merge duplicate activities"** folds the same real activity imported
  from two sources (e.g. Strava export + Garmin sync) into one row, keeping
  whichever source has richer metadata; **"Strip Garmin 'V2' type
  suffixes"** removes Garmin's internal activity-type version suffixes
  (e.g. "Kayaking V2" → "Kayaking"), which also happens automatically on
  every future import. Separately, **"Merge legacy types"** merges
  Hiking/Walking into "Hiking" and Kayaking/Rowing into "Kayaking" for
  activities before a date you choose - this is deliberately manual and
  date-scoped rather than automatic, since it reflects which watch you were
  using at the time (older devices offering a more limited choice of
  activity types), not which service the activity was imported through.
- Uploading Strava's `activities.csv` (included in its full account export,
  alongside the `activities/` folder) recovers your real activity titles
  for anything that came from a raw GPX/FIT/TCX file - this works even if
  uploaded separately/later, to backfill names on what's already imported.
- Matching recomputes over *all* activities every time you import new ones
  or click "Recompute matches". This is O(n²) but resampled tracks are only
  40 points each, so it stays fast into the low thousands of runs — fine
  for personal use.
- This is built for a single user (you). There's no login system, so don't
  expose port 8000 to the public internet without putting it behind your
  own auth/reverse proxy.
- Only `Run` and `TrailRun` activity types are pulled from Strava by
  default — edit the filter in `app/strava_client.py` if you also want
  walks or hikes matched.
