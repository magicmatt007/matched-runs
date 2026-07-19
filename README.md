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

Three independent ways to import runs — use any combination:

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
