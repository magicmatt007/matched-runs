# Matched Runs

Self-hosted route matching for your GPS activities - groups runs (or rides,
hikes, etc.) by route, the way Strava's paid "matched runs" feature does.

## Configuration

All fields are optional - leave blank to skip that feature.

| Option | Description |
|---|---|
| `garmin_email` / `garmin_password` | Enables automatic Garmin sync. See the main README's caveats about this being unofficial. |
| `garmin_sync_interval_minutes` | How often to check Garmin for new activities (default 120). |
| `strava_client_id` / `strava_client_secret` | Enables Strava sync. Create an API app at strava.com/settings/api first. |
| `strava_redirect_uri` | Only needed for Strava OAuth - see the note below about ingress. |
| `match_distance_threshold_m` / `match_length_tolerance` | Route-matching sensitivity tuning. |

## Ingress vs. direct port

This app works through Home Assistant's sidebar (ingress) with no setup.
However, **Strava's OAuth "Connect Strava" button needs a stable, fixed
callback URL** - ingress URLs are dynamic per-session tokens, so that flow
only works via the app's direct port (enabled by default in this app's
network settings), not through ingress. If you don't use Strava sync, you
can ignore this and close the direct port.

## One-time Garmin login (accounts with MFA/2FA)

If your Garmin account has MFA enabled, run this once via the
**Terminal & SSH** add-on (find the exact container name first if unsure):

    docker ps | grep matched_runs
    docker exec -it <container_name> python garmin_login.py

Follow the prompts, including entering your MFA code. This persists a
session under this app's `/data` volume, so it's picked up automatically
afterwards - you shouldn't need to do this again for about a year.