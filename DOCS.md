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
However, **Strava's OAuth "Connect Strava" button needs a stable, fixed
callback URL** - ingress URLs are dynamic per-session tokens, so that flow
only works via the app's direct port (enabled by default in this app's
network settings), not through ingress. If you don't use Strava sync, you
can ignore this and close the direct port.
