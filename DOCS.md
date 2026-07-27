# Matched Runs

Self-hosted route matching for your GPS activities - groups runs (or rides,
hikes, etc.) by route, the way Strava's paid "matched runs" feature does.

## Configuration

All fields are optional - leave blank to skip that feature.

| Option | Description |
|---|---|
| `garmin_email` / `garmin_password` | Optional fallback for automatic Garmin sync. You don't need to set these - use the "Connect Garmin" button in the app itself instead, which handles login (including MFA) entirely through the web UI. |
| `garmin_sync_interval_minutes` | How often to check Garmin for new activities (default 120). |
| `strava_client_id` / `strava_client_secret` | Enables Strava sync. Create an API app at strava.com/settings/api first. |
| `strava_redirect_uri` | Only needed for Strava OAuth - see the note below about ingress. |
| `match_distance_threshold_m` / `match_length_tolerance` | Route-matching sensitivity tuning. |

## Connecting Garmin

Click **"Connect Garmin"** on the app's homepage and log in with your Garmin
credentials directly through the web UI - including entering an MFA code if
your account has that enabled. No terminal or docker commands needed. This
creates a session that's reused automatically afterwards (valid for roughly
a year), and works identically whether you're accessing the app through
ingress or its direct port.

Your password is only used for that one login and isn't stored - only the
resulting session token is kept.

## Ingress vs. direct port

This app works through Home Assistant's sidebar (ingress) with no setup.
However, two things need the app's direct port instead:

- **Strava's OAuth "Connect Strava" button needs a stable, fixed callback
  URL** - ingress URLs are dynamic per-session tokens, so that flow only
  works via the direct port (enabled by default in this app's network
  settings), not through ingress.
- **Importing a database larger than 16MB** (Import & Sync → Database
  export/import) fails through ingress with `Maximum request body size
  16777216 exceeded` - this is a hardcoded limit in Home Assistant's
  Supervisor itself (not something this app can work around), the same
  issue other add-ons like OctoPrint have hit for large file uploads. Go
  to `http://<your-ha-host>:8000/manage` directly instead of the sidebar
  for this specific action; export/download isn't affected, only import.

If you don't need either of those, you can ignore this and close the
direct port.

## Rename activities by city

Import & Sync has a "Rename all activities by city" button, which renames
every activity to "[City] [Activity Type]" (e.g. "Zurich Running") based on
its GPS start point. This looks up each location via OpenStreetMap's free
Nominatim geocoding service, which means:

- The app needs outbound internet access to `nominatim.openstreetmap.org`
  (it already needs this for the OpenStreetMap map tiles, so this isn't a
  new type of requirement, just a different destination).
- Nominatim's usage policy caps lookups at roughly one per second, so this
  can take a while for a large history - a progress bar with an ETA is
  shown once started. Activities that share a location (e.g. many runs of
  the same route) only need one lookup between them, so this is much
  faster on a re-run than the first time.
- Indoor activities with no GPS track are left untouched, since there's no
  location to look up.
