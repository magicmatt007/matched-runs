# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-hosted "matched runs" tool (FastAPI + SQLite + server-rendered
Jinja2): it groups GPS activities (runs, rides, hikes, ...) by route, the
same idea as Strava's paid "matched runs" feature. Single-user, no login
system - not meant to be exposed to the public internet.

The same codebase ships two ways from one Dockerfile: standalone `docker
compose`, and as a Home Assistant "app" (add-on) - `config.yaml` is the
Supervisor manifest for the latter. Both read the same settings; Home
Assistant's Configuration tab options map to the same names as the `.env`
vars (uppercased) - see `docker_entrypoint.py`, which loads
`/data/options.json` when present (HA) and exports each key as an env var
before `.env`-style config is read.

## Running / developing

```bash
cp .env.example .env    # only needed for Strava sync / non-default matching tuning
docker compose up --build -d
```
Then open http://localhost:8000. All state lives in `./data` (SQLite DB at
`data/app.db`, Garmin session tokens, uploaded GPX cache) - it's a bind
mount, so it persists across `docker compose down`/rebuilds; only a
`docker compose down -v` or manually touching `./data` would lose it.

After changing code, rebuild before testing: `docker compose build && docker
compose up -d`. `docker compose logs -f matched-runs` for logs.

There is no test suite (no pytest, no fixtures). What CI (`.github/workflows/ci.yml`)
runs instead - use the same commands locally before considering a change done:

```bash
python -m py_compile app/*.py garmin_login.py docker_entrypoint.py  # syntax
python scripts/check_routes.py       # catches a decorator landing on the wrong function
python scripts/check_templates.py    # Jinja2 template syntax (parses, doesn't render)
python -c "import app.main"          # import-time sanity check (catches missing deps etc.)
python -c "import yaml; yaml.safe_load(open('config.yaml'))"
```
`check_routes.py`/`check_templates.py` exist specifically because there's no
test suite to catch these classes of mistake otherwise - read their
docstrings before assuming a check is redundant.

For anything beyond a syntax/import check, verify by actually running the
app (`docker compose up --build -d`) and exercising the route/page through
the browser or `curl` - there's no other way to catch a runtime-only bug
here.

## Versioning / releases

Every user-facing change bumps `version` in `config.yaml` and gets a new
top entry in `CHANGELOG.md` (both in the same commit as the change). Commit
messages follow `vX.Y.Z: <summary>`, mirroring the changelog entry -
`git log` is effectively a second changelog. There's no separate release/tag
step beyond that.

## Architecture

### `app/main.py` is the whole app

One large FastAPI file (~2500 lines) holding every route, grouped by
`# ---------------- Section ----------------` comments (training log,
type merging, DB export/import, rename-by-city, Strava OAuth/sync, Garmin
sync...). There's no router-splitting/blueprints - when adding a route,
find the right section by grepping for its comment banner, or add a new one
at the end following the same pattern.

### Background jobs

Anything slow (bulk import, Garmin/Strava sync, "Recompute matches",
"Rename by city", DB import) follows the same pattern, don't invent a new
one:
- A module-level dict (e.g. `_current_import_job = {"active": False}`)
  holds progress state.
- The POST route validates, sets `job["active"] = True` plus initial
  fields, and fires the real work via
  `_spawn_background_task(asyncio.to_thread(_run_x_sync, ..., job))` -
  returns immediately (redirect), doesn't block the request.
- The worker function mutates the shared `job` dict as it progresses
  (`job["processed"] += 1`, etc.) and sets `job["phase"] = "done"`/`"error"`
  in a `finally`.
- A `GET /manage/x-status` endpoint just returns the `job` dict as JSON.
- `manage.html`'s own `<script>` polls that endpoint and renders a progress
  bar/ETA - each job has its own poll loop IIFE in that one template file.

CPU-bound work (parsing thousands of GPX/FIT/TCX files) additionally uses a
`ProcessPoolExecutor` (see `MAX_PARSE_WORKERS`/`_run_import_sync`) - SQLite
writes are still done sequentially on the main worker thread afterward,
never from the pool.

### Data model (`app/models.py`)

Two tables: `Activity` and `RouteGroup` (one-to-many). An `Activity`'s
identity for dedup purposes is `(source, external_id)` (unique constraint) -
`source` is `"gpx"`/`"fit"`/`"tcx"` (file import), `"strava"`, or
`"garmin"` (live sync). `external_id` is the *basename* of the uploaded
file for file-based sources (see the long comment on that in `_save_activity`
- normalized on migration too), or the real numeric activity ID for live
sync. Cross-source duplicates (the same real activity imported twice, from
different sources) are resolved by `find_cross_source_duplicate` +
`SOURCE_PRIORITY` in `app/matcher.py` (matched by close start time + route
geometry, not by ID) - richer sources win and absorb the row rather than
creating a second one.

No Alembic/migration framework - `app/database.py`'s `run_migrations()` is
a hand-rolled "add any columns the ORM model has that the actual sqlite
table doesn't yet" step, run once at startup. Adding a column to `Activity`
means adding both the `Column(...)` in `models.py` *and* an
`ensure_column(...)` call in `run_migrations()`.

### Route matching (`app/matcher.py`)

Every track is resampled to 40 evenly-spaced-by-distance points
(`resample_track`), then compared point-by-point against every other
activity's resampled track (both directions, so an out-and-back route run
either way still matches). Within `MATCH_DISTANCE_THRESHOLD_M` avg
deviation and `MATCH_LENGTH_TOLERANCE` relative length → match; matches are
transitively grouped into `RouteGroup`s via union-find
(`rebuild_groups`/`incremental_rebuild_groups`). O(n²) over full activity
count, but 40-point tracks keep it fast into the low thousands of
activities - `incremental_rebuild_groups` (compare only new activities
against existing groups) is used instead of a full `rebuild_groups` when
few activities were added, to avoid the full O(n²) pass on every small
import.

### Import pipeline

Raw files → one of `app/gpx_parser.py` / `app/fit_parser.py` /
`app/tcx_parser.py` (each returns the same dict shape: points, distance,
duration, elevation/HR profiles, etc.) → `_save_activity` in `main.py`
(dedup + cross-source-duplicate handling) → route matching.

A `.fit` file has no free-text title and its own filename's embedded
number is *not* a usable external activity ID (confirmed against Garmin's
real API - looks plausible, 404s). `app/garmin_summarized.py` recovers
both from a Garmin account export's `DI-Connect-Fitness/*_summarizedActivities.json`,
matched by start time (no shared filename/ID to join on) since neither
file references the other directly. `app/strava_csv.py` does the
equivalent for Strava exports, matched by exact filename instead (Strava's
CSV does reference it directly). A `.zip` upload (Garmin's export wraps
each raw upload in its own individual zip) is expanded into individual
files *before* handing off to the parse worker pool, not inside it - see
`_expand_zip_entries`'s docstring for why (progress-bar granularity and
work distribution both depend on it).

### Ingress-awareness

Every route redirect goes through `local_redirect()`/`ingress_prefix()`
rather than a bare `RedirectResponse` - Home Assistant's ingress proxy
serves the app under a dynamic per-request path
(`X-Ingress-Path`/`x-ingress-path` header), and a bare absolute redirect
would bounce the user out of that proxied session. Every template computes
its own `base_path` the same way at the top (`{% set base_path =
request.headers.get('X-Ingress-Path', '') %}`) and prefixes every internal
`href`/`action`/`fetch()` URL with it - copy this pattern for any new page,
not a hardcoded absolute path.

### Templates / frontend

Server-rendered Jinja2 (`app/templates/`), no JS build step or framework -
plain `<script>` blocks per page, a few small shared vanilla-JS files in
`app/static/` (swipe nav between activities, mobile detection, local-time
display, a CSP-safe `data-confirm` dialog handler). Charts on the activity
detail page (`activity.html`) are hand-rolled inline SVG, not a charting
library. `base.html` + `_filters.html`/`_pagination.html`/`_table_controls.html`
are the shared partials.

## External integrations

- **Garmin**: no public personal-use API - `app/garmin_client.py` wraps the
  unofficial `garminconnect` package (logs in as if it were the mobile
  app). Can break when Garmin changes their login flow; check
  https://github.com/cyberjunky/python-garminconnect/issues before assuming
  a sync bug is this app's fault.
- **Strava**: official OAuth API (`app/strava_client.py`), but its activity
  list only returns a *simplified* polyline, not the full-resolution
  track - fine for matching, less detailed on the map view than a raw
  GPX/FIT import.
- **OpenStreetMap**: Leaflet map tiles (vendored into the Docker image at
  build time, not fetched from a CDN at runtime) and Nominatim for the
  "Rename all activities by city" feature (rate-limited to ~1 req/s by
  Nominatim's usage policy, hence that feature's own progress bar).
