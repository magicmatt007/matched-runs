import json
import time
import os
import gzip
import logging
import asyncio
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, UploadFile, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import Base, engine, get_db, run_migrations, SessionLocal
from app.models import Activity, RouteGroup, StravaToken, GarminSyncState
from app.gpx_parser import parse_gpx_bytes
from app.fit_parser import parse_fit_bytes
from app.tcx_parser import parse_tcx_bytes
from app.matcher import resample_track, rebuild_groups, incremental_rebuild_groups, find_cross_source_duplicate, merge_duplicate_activities, SOURCE_PRIORITY
from app.polyline_util import decode_polyline
from app.type_normalize import normalize_activity_type, merge_legacy_type
from app.strava_csv import parse_strava_activities_csv
from app import strava_client
from app import garmin_client

logger = logging.getLogger("matched_runs")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

GARMIN_SYNC_INTERVAL_MINUTES = int(os.environ.get("GARMIN_SYNC_INTERVAL_MINUTES", 120))

Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(title="Matched Runs")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def ingress_prefix(request: Request) -> str:
    """Home Assistant's ingress proxy serves this app under a dynamic
    per-session path (e.g. /api/hassio_ingress/<token>), passed via this
    header on every proxied request. Empty string when not behind ingress,
    i.e. standalone docker compose - so nothing changes for that case."""
    return request.headers.get("x-ingress-path", "")


def local_redirect(request: Request, path: str, status_code: int = 303) -> RedirectResponse:
    """Use this instead of a bare RedirectResponse(path) for any redirect
    targeting a path within this app, so the browser doesn't get bounced
    out of the ingress session when running under Home Assistant."""
    return RedirectResponse(ingress_prefix(request) + path, status_code=status_code)


def _save_activity(db: Session, source: str, external_id: str, name: str,
                    points, distance_m: float, duration_s, start_time,
                    activity_type: str = None):
    """Returns ("added", activity), ("updated", activity), or ("unchanged", activity)."""
    activity_type = normalize_activity_type(activity_type or "Other")
    existing = db.query(Activity).filter_by(source=source, external_id=external_id).first()

    if existing:
        # Re-imported (e.g. re-uploading the same export after an app update
        # added new fields, such as activity_type). Backfill in place rather
        # than silently skipping, so re-uploading is enough to pick up new
        # fields without needing to wipe and reimport everything.
        changed = False
        if existing.activity_type != activity_type:
            existing.activity_type = activity_type
            changed = True
        if name and existing.name != name:
            existing.name = name
            changed = True
        return ("updated" if changed else "unchanged", existing)

    # Cross-source duplicate check: the same real-world activity imported
    # from a different source (e.g. a bulk Strava export AND live Garmin
    # sync both bringing in the same hike). Detected by close start time +
    # matching route geometry, since sources sometimes disagree slightly on
    # exact distance/duration.
    dup = find_cross_source_duplicate(db, points, distance_m, start_time, exclude_source=source)
    if dup is not None:
        if SOURCE_PRIORITY.get(source, 0) > SOURCE_PRIORITY.get(dup.source, 0):
            # New source is richer (e.g. a live Garmin/Strava sync arriving
            # after a raw file upload) - fold into the existing row instead
            # of keeping two.
            resampled = resample_track(points)
            dup.source = source
            dup.external_id = external_id
            dup.name = name or dup.name
            dup.activity_type = activity_type
            dup.distance_m = distance_m
            dup.duration_s = duration_s
            dup.start_time = start_time
            dup.full_points_json = json.dumps(points)
            dup.resampled_points_json = json.dumps(resampled)
            return ("updated", dup)
        else:
            return ("unchanged", dup)  # lower/equal priority source - discard the new one

    resampled = resample_track(points)
    activity = Activity(
        source=source,
        external_id=external_id,
        name=name or "Run",
        activity_type=activity_type,
        start_time=start_time,
        distance_m=distance_m,
        duration_s=duration_s,
        full_points_json=json.dumps(points),
        resampled_points_json=json.dumps(resampled),
    )
    db.add(activity)
    return ("added", activity)


def format_duration_hms(seconds):
    """1234.5 -> '00:20:34'"""
    if seconds is None:
        return "-"
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "-"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_pace(activity):
    """Pace in min:sec per km, e.g. '5:12 /km'."""
    if not activity.duration_s or not activity.distance_m:
        return "-"
    distance_km = activity.distance_m / 1000.0
    if distance_km <= 0:
        return "-"
    pace_seconds_per_km = activity.duration_s / distance_km
    m, s = divmod(int(round(pace_seconds_per_km)), 60)
    return f"{m}:{s:02d} /km"


templates.env.filters["hms"] = format_duration_hms
templates.env.filters["pace"] = format_pace


@app.get("/manage", response_class=HTMLResponse)
def manage_page(request: Request, db: Session = Depends(get_db)):
    groups = (
        db.query(RouteGroup)
        .order_by(RouteGroup.avg_distance_m.desc())
        .all()
    )
    group_counts = {g.id: len(g.activities) for g in groups}
    groups = sorted(groups, key=lambda g: group_counts[g.id], reverse=True)

    ungrouped = (
        db.query(Activity)
        .filter(Activity.group_id.is_(None))
        .order_by(Activity.start_time.desc())
        .all()
    )
    total_activities = db.query(func.count(Activity.id)).scalar()
    strava_connected = db.query(StravaToken).first() is not None

    upload_result = None
    if "imported" in request.query_params:
        upload_result = {
            "imported": request.query_params.get("imported", "0"),
            "updated": request.query_params.get("updated", "0"),
            "skipped": request.query_params.get("skipped", "0"),
            "duplicates": request.query_params.get("duplicates", "0"),
            "unsupported": request.query_params.get("unsupported", "0"),
        }

    return templates.TemplateResponse("manage.html", {
        "request": request,
        "groups": groups,
        "group_counts": group_counts,
        "ungrouped": ungrouped,
        "total_activities": total_activities,
        "strava_connected": strava_connected,
        "strava_configured": strava_client.is_configured(),
        "upload_result": upload_result,
        "garmin_configured": garmin_client.is_configured(),
        "garmin_has_session": garmin_client.has_saved_session(),
        "garmin_sync_interval": GARMIN_SYNC_INTERVAL_MINUTES,
    })


@app.post("/upload")
async def upload_gpx(request: Request, db: Session = Depends(get_db)):
    # FastAPI's default File(...) injection caps multipart requests at 1000
    # files/fields as a DoS safeguard. Years of activity history easily
    # exceeds that, so parse the form manually with a much higher ceiling -
    # this is a personal, single-user app, so that tradeoff is fine here.
    form = await request.form(max_files=50_000, max_fields=50_000)
    files = form.getlist("files")
    logger.info(
        "Upload request received: %d form keys total, %d items under 'files'. Types: %s",
        len(form.keys()), len(files), [type(x).__name__ for x in files[:5]],
    )

    added, updated, unchanged, skipped, unsupported_ext = 0, 0, 0, 0, 0
    seen_extensions = set()
    added_activities = []

    # Strava's bulk export includes a top-level activities.csv mapping each
    # exported file to the title you actually gave it on Strava. If it's
    # part of this upload, pull it out first and use it to name-override
    # anything we're about to import (and anything already imported before).
    name_overrides = {}
    activity_files = []
    for f in files:
        if hasattr(f, "filename") and (f.filename or "").lower().endswith("activities.csv"):
            try:
                content = await f.read()
                found = parse_strava_activities_csv(content)
                name_overrides.update(found)
                logger.info("Loaded %d titles from %s", len(found), f.filename)
            except Exception as e:
                logger.warning("Failed to parse %s: %s", f.filename, e)
        else:
            activity_files.append(f)
    files = activity_files

    for f in files:
        # Duck-type instead of isinstance(f, UploadFile): depending on
        # Starlette/FastAPI version, objects returned by request.form() may
        # not be the exact same class object as the imported UploadFile,
        # which would silently make isinstance() reject every file.
        if not (hasattr(f, "filename") and hasattr(f, "read")):
            logger.warning("Skipping non-file form field under 'files': %r", f)
            continue
        content = await f.read()
        lower_name = (f.filename or "").lower()

        # Strava's bulk export gzips some activity files (e.g. "123.gpx.gz").
        if lower_name.endswith(".gz"):
            try:
                content = gzip.decompress(content)
            except Exception as e:
                logger.warning("Failed to gunzip %s: %s", f.filename, e)
                skipped += 1
                continue
            lower_name = lower_name[:-3]  # strip trailing ".gz"

        ext = lower_name.rsplit(".", 1)[-1] if "." in lower_name else "(none)"
        seen_extensions.add(ext)

        try:
            if lower_name.endswith(".fit"):
                parsed = parse_fit_bytes(content, fallback_name=f.filename)
            elif lower_name.endswith(".gpx"):
                parsed = parse_gpx_bytes(content, fallback_name=f.filename)
            elif lower_name.endswith(".tcx"):
                parsed = parse_tcx_bytes(content, fallback_name=f.filename)
            else:
                unsupported_ext += 1
                skipped += 1
                continue
        except Exception as e:
            # Corrupt file, indoor activity with no GPS, unsupported FIT
            # message layout, etc - skip it and keep processing the rest of
            # the batch rather than aborting the whole import.
            logger.warning("Failed to parse %s: %s", f.filename, e)
            skipped += 1
            continue

        if lower_name.endswith(".gpx"):
            source = "gpx"
        elif lower_name.endswith(".tcx"):
            source = "tcx"
        else:
            source = "fit"

        base_filename = (f.filename or "").rsplit("/", 1)[-1].strip().lower()
        final_name = name_overrides.get(base_filename) or parsed["name"]

        status, activity = _save_activity(
            db, source=source, external_id=f.filename,
            name=final_name, points=parsed["points"],
            distance_m=parsed["distance_m"], duration_s=parsed["duration_s"],
            start_time=parsed["start_time"], activity_type=parsed.get("activity_type"),
        )
        if status == "added":
            added += 1
            added_activities.append(activity)
        elif status == "updated":
            updated += 1
        else:
            unchanged += 1

    # Backfill names on already-imported activities too, in case
    # activities.csv was uploaded separately from (before or after) the
    # actual activity files.
    if name_overrides:
        for act in db.query(Activity).filter(Activity.source.in_(["gpx", "fit", "tcx"])).all():
            base = (act.external_id or "").rsplit("/", 1)[-1].strip().lower()
            better_name = name_overrides.get(base)
            if better_name and act.name != better_name:
                act.name = better_name
                updated += 1

    db.flush()  # assign primary keys to newly added activities before reading their ids
    added_ids = [a.id for a in added_activities]
    db.commit()

    if updated:
        # A metadata-only update (name/type backfill) doesn't need
        # re-matching, but a cross-source-duplicate merge DOES change an
        # activity's geometry - since upload_gpx can't tell those apart
        # here without extra bookkeeping, play it safe with a full rebuild
        # whenever any update happened at all.
        rebuild_groups(db)
    elif added_ids:
        if len(added_ids) > 20:
            # A big bulk import (e.g. years of history in one folder
            # upload) has enough new activities that comparing each one
            # against everything isn't meaningfully cheaper than a full
            # rebuild - just do the simple, obviously-correct thing.
            rebuild_groups(db)
        else:
            incremental_rebuild_groups(db, added_ids)

    logger.info(
        "Upload finished: %s added, %s updated, %s unchanged, %s skipped (%s unsupported extension). Extensions seen: %s",
        added, updated, unchanged, skipped, unsupported_ext, sorted(seen_extensions),
    )
    return local_redirect(
        request,
        f"/manage?imported={added}&updated={updated}&skipped={skipped}"
        f"&duplicates={unchanged}&unsupported={unsupported_ext}",
    )


PAGE_SIZE_OPTIONS = [25, 50, 100, 200]
DEFAULT_PAGE_SIZE = 50


def paginate(query, page: int, page_size: int):
    page = max(page or 1, 1)
    if not page_size or page_size not in PAGE_SIZE_OPTIONS:
        page_size = DEFAULT_PAGE_SIZE
    total = query.count()
    total_pages = max(1, -(-total // page_size))  # ceil div
    page = min(page, total_pages)
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return items, page, total_pages, total, page_size


SORT_COLUMNS = {
    "date": Activity.start_time,
    "name": Activity.name,
    "type": Activity.activity_type,
    "distance": Activity.distance_m,
    "duration": Activity.duration_s,
    # Pace isn't a stored column - sort by the same ratio used to display
    # it. SQLite returns NULL (sorts first) rather than erroring on a
    # distance_m of 0, which is an acceptable edge case here.
    "pace": Activity.duration_s * 1000.0 / Activity.distance_m,
}

# Query param keys used for the per-column filters, shared between the home
# activity list and the group detail page's activity table.
FILTER_PARAM_KEYS = [
    "name_filter", "date_from", "date_to",
    "distance_min", "distance_max", "duration_min", "duration_max",
    "pace_min", "pace_max",
]


def apply_sort_and_filters(query, request: Request):
    """Applies the shared column filters + sort order (query params: sort,
    dir, name_filter, date_from, date_to, distance_min/max in km,
    duration_min/max in minutes) to an Activity query."""
    qp = request.query_params

    name_filter = qp.get("name_filter", "").strip()
    if name_filter:
        query = query.filter(Activity.name.ilike(f"%{name_filter}%"))

    date_from = qp.get("date_from", "").strip()
    if date_from:
        try:
            query = query.filter(Activity.start_time >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    date_to = qp.get("date_to", "").strip()
    if date_to:
        try:
            # Inclusive of the whole selected day.
            query = query.filter(Activity.start_time < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass

    distance_min = qp.get("distance_min", "").strip()
    if distance_min:
        try:
            query = query.filter(Activity.distance_m >= float(distance_min) * 1000)
        except ValueError:
            pass
    distance_max = qp.get("distance_max", "").strip()
    if distance_max:
        try:
            query = query.filter(Activity.distance_m <= float(distance_max) * 1000)
        except ValueError:
            pass

    duration_min = qp.get("duration_min", "").strip()
    if duration_min:
        try:
            query = query.filter(Activity.duration_s >= float(duration_min) * 60)
        except ValueError:
            pass
    duration_max = qp.get("duration_max", "").strip()
    if duration_max:
        try:
            query = query.filter(Activity.duration_s <= float(duration_max) * 60)
        except ValueError:
            pass

    # Pace is duration_s / (distance_m/1000), i.e. seconds per km. Filters
    # are expressed as decimal minutes/km (e.g. 5.5 = 5:30/km), same
    # convention as the duration filter above. Written as a multiplication
    # rather than dividing by distance_m, so it doesn't choke on any
    # zero-distance rows the way a direct division would.
    pace_min = qp.get("pace_min", "").strip()
    if pace_min:
        try:
            seconds_per_km = float(pace_min) * 60
            query = query.filter(Activity.duration_s >= seconds_per_km * (Activity.distance_m / 1000.0))
        except ValueError:
            pass
    pace_max = qp.get("pace_max", "").strip()
    if pace_max:
        try:
            seconds_per_km = float(pace_max) * 60
            query = query.filter(Activity.duration_s <= seconds_per_km * (Activity.distance_m / 1000.0))
        except ValueError:
            pass

    sort = qp.get("sort", "date")
    if sort not in SORT_COLUMNS:
        sort = "date"
    direction = qp.get("dir", "desc")
    if direction not in ("asc", "desc"):
        direction = "desc"

    col = SORT_COLUMNS[sort]
    query = query.order_by(col.asc() if direction == "asc" else col.desc())

    return query, sort, direction


def build_carry_params(request: Request, include_type: bool = True):
    """Every currently-active filter/sort query param, for reuse in
    pagination links and sortable column headers so they don't reset each
    other. Deliberately excludes 'page' - changing a filter or sort should
    land back on page 1."""
    qp = request.query_params
    params = {}
    if include_type and qp.get("type"):
        params["type"] = qp.get("type")
    for key in FILTER_PARAM_KEYS:
        v = qp.get(key, "").strip()
        if v:
            params[key] = v
    if qp.get("sort"):
        params["sort"] = qp.get("sort")
    if qp.get("dir"):
        params["dir"] = qp.get("dir")
    return params


@app.get("/activities")
def activities_old_url_redirect(request: Request, type: str = None, page: int = 1,
                                 page_size: int = DEFAULT_PAGE_SIZE):
    """The 'All Activities' page used to live at /activities - it's now the
    home page at /. Keep old bookmarks/links working."""
    params = f"?page={page}&page_size={page_size}" + (f"&type={type}" if type else "")
    return local_redirect(request, f"/{params}")


@app.get("/", response_class=HTMLResponse)
def activities_list(request: Request, type: str = None, page: int = 1,
                     page_size: int = DEFAULT_PAGE_SIZE, db: Session = Depends(get_db)):
    query = db.query(Activity)
    if type:
        query = query.filter(Activity.activity_type == type)
    query, sort, direction = apply_sort_and_filters(query, request)
    activities, page, total_pages, total, page_size = paginate(query, page, page_size)

    types = sorted({
        row[0] for row in db.query(Activity.activity_type).distinct().all() if row[0]
    })

    return templates.TemplateResponse("activities.html", {
        "request": request,
        "activities": activities,
        "types": types,
        "selected_type": type,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "page_size": page_size,
        "page_size_options": PAGE_SIZE_OPTIONS,
        "sort": sort,
        "dir": direction,
        "filters": {k: request.query_params.get(k, "") for k in FILTER_PARAM_KEYS},
        "carry_params": build_carry_params(request),
    })


@app.get("/group/{group_id}", response_class=HTMLResponse)
def group_detail(group_id: int, request: Request, page: int = 1,
                  page_size: int = DEFAULT_PAGE_SIZE, db: Session = Depends(get_db)):
    group = db.query(RouteGroup).filter_by(id=group_id).first()
    if not group:
        return local_redirect(request, "/")

    activities_query = db.query(Activity).filter_by(group_id=group_id)
    activities_query, sort, direction = apply_sort_and_filters(activities_query, request)
    activities, page, total_pages, total, page_size = paginate(activities_query, page, page_size)

    # Pace-over-time chart data: always chronological (oldest to newest),
    # independent of whatever sort the table itself is using.
    chart_activities = sorted(
        (a for a in group.activities if a.start_time and a.duration_s and a.distance_m),
        key=lambda a: a.start_time,
    )
    chart_points = [
        {
            "date": a.start_time.strftime("%Y-%m-%d"),
            "pace_s_per_km": a.duration_s / (a.distance_m / 1000.0),
            "activity_id": a.id,
        }
        for a in chart_activities
    ]

    return templates.TemplateResponse("group.html", {
        "request": request,
        "group": group,
        "activities": activities,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "page_size": page_size,
        "page_size_options": PAGE_SIZE_OPTIONS,
        "sort": sort,
        "dir": direction,
        "filters": {k: request.query_params.get(k, "") for k in FILTER_PARAM_KEYS},
        "carry_params": build_carry_params(request, include_type=False),
        "chart_points": chart_points,
    })


@app.get("/activity/{activity_id}", response_class=HTMLResponse)
def activity_detail(activity_id: int, request: Request, db: Session = Depends(get_db)):
    activity = db.query(Activity).filter_by(id=activity_id).first()
    return templates.TemplateResponse("activity.html", {"request": request, "activity": activity})


@app.post("/normalize-types")
def normalize_types_route(request: Request, db: Session = Depends(get_db)):
    changed = 0
    for act in db.query(Activity).all():
        new_type = normalize_activity_type(act.activity_type)
        if new_type != act.activity_type:
            act.activity_type = new_type
            changed += 1
    if changed:
        db.commit()
        rebuild_groups(db)  # renamed types can change which activities group together
    logger.info("Type normalization (version suffixes): %d activities updated", changed)
    return local_redirect(request, f"/manage?imported=0&updated={changed}&skipped=0&duplicates=0&unsupported=0")


DEFAULT_LEGACY_TYPE_CUTOFF = "2026-04-01"


@app.post("/merge-legacy-types")
def merge_legacy_types_route(request: Request, cutoff_date: str = Form(DEFAULT_LEGACY_TYPE_CUTOFF),
                              db: Session = Depends(get_db)):
    try:
        cutoff_dt = datetime.strptime(cutoff_date, "%Y-%m-%d")
    except ValueError:
        return HTMLResponse(
            f"<p>Invalid date: {cutoff_date!r} (expected YYYY-MM-DD)</p>"
            f"<p><a href='{ingress_prefix(request)}/'>back</a></p>",
            status_code=400,
        )

    changed = 0
    activities = (
        db.query(Activity)
        .filter(Activity.start_time.isnot(None))
        .filter(Activity.start_time < cutoff_dt)
        .all()
    )
    for act in activities:
        new_type = merge_legacy_type(act.activity_type)
        if new_type != act.activity_type:
            act.activity_type = new_type
            changed += 1
    if changed:
        db.commit()
        rebuild_groups(db)  # merged types can change which activities group together
    logger.info("Legacy type merge (activities before %s): %d activities updated", cutoff_date, changed)
    return local_redirect(request, f"/manage?imported=0&updated={changed}&skipped=0&duplicates=0&unsupported=0")


@app.post("/dedupe")
def dedupe_route(request: Request, db: Session = Depends(get_db)):
    removed = merge_duplicate_activities(db)
    logger.info("Deduplication: merged/removed %d duplicate activities", removed)
    return local_redirect(request, f"/manage?imported=0&updated={removed}&skipped=0&duplicates=0&unsupported=0")


@app.post("/rebuild")
def rebuild(request: Request, db: Session = Depends(get_db)):
    rebuild_groups(db)
    return local_redirect(request, "/manage")


# ---------------- Strava OAuth + sync ----------------

@app.get("/strava/login")
def strava_login():
    if not strava_client.is_configured():
        return HTMLResponse(
            "Strava API not configured. Set STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET in .env", status_code=400
        )
    # External redirect to Strava's own site - no ingress-path fix needed here.
    return RedirectResponse(strava_client.get_authorize_url())


@app.get("/strava/callback")
def strava_callback(request: Request, code: str = None, error: str = None, scope: str = None,
                     db: Session = Depends(get_db)):
    if error or not code:
        return local_redirect(request, "/manage")

    data = strava_client.exchange_code_for_token(code)

    if not scope or "activity:read_all" not in scope:
        prefix = ingress_prefix(request)
        return HTMLResponse(
            "<p>Strava did not grant the <code>activity:read_all</code> permission "
            f"(granted scope: <code>{scope}</code>).</p>"
            "<p>This usually happens when this app was authorized before with a "
            "narrower scope, and Strava is silently reusing that old grant. Fix: "
            "go to <a href='https://www.strava.com/settings/apps' target='_blank'>"
            "strava.com/settings/apps</a>, revoke access for this app, then "
            f"<a href='{prefix}/strava/login'>connect again</a>.</p>",
            status_code=400,
        )

    token = db.query(StravaToken).first()
    if not token:
        token = StravaToken(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            athlete_id=str(data.get("athlete", {}).get("id", "")),
            scope=scope,
        )
        db.add(token)
    else:
        token.access_token = data["access_token"]
        token.refresh_token = data["refresh_token"]
        token.expires_at = data["expires_at"]
        token.scope = scope
    db.commit()
    return local_redirect(request, "/manage")


@app.post("/strava/disconnect")
def strava_disconnect(request: Request, db: Session = Depends(get_db)):
    db.query(StravaToken).delete()
    db.commit()
    return local_redirect(request, "/manage")


@app.post("/strava/sync")
def strava_sync(request: Request, db: Session = Depends(get_db)):
    token = db.query(StravaToken).first()
    if not token:
        return local_redirect(request, "/manage")

    try:
        access_token = strava_client.ensure_valid_token(token)
        db.commit()
        activities = strava_client.fetch_activities(access_token)
    except strava_client.StravaAuthError as e:
        # Token is dead either way (expired/revoked/wrong scope) - clear it so
        # the UI goes back to "Connect Strava" instead of retrying the same
        # broken token forever.
        db.query(StravaToken).delete()
        db.commit()
        prefix = ingress_prefix(request)
        return HTMLResponse(
            f"<p>{e}</p>"
            "<p>Your stored Strava connection was cleared. Please "
            f"<a href='{prefix}/strava/login'>connect Strava</a> again — if this keeps "
            "happening, also revoke the app at "
            "<a href='https://www.strava.com/settings/apps' target='_blank'>"
            "strava.com/settings/apps</a> first, then reconnect.</p>"
            f"<p><a href='{prefix}/'>back</a></p>",
            status_code=401,
        )

    added, updated = 0, 0
    added_activities = []
    for a in activities:
        polyline = (a.get("map") or {}).get("summary_polyline")
        if not polyline:
            continue
        points = decode_polyline(polyline)
        if len(points) < 2:
            continue
        start_time = None
        if a.get("start_date"):
            try:
                start_time = datetime.fromisoformat(a["start_date"].replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass
        status, activity = _save_activity(
            db, source="strava", external_id=str(a["id"]),
            name=a.get("name", "Run"), points=points,
            distance_m=a.get("distance", 0.0), duration_s=a.get("moving_time"),
            start_time=start_time, activity_type=a.get("type"),
        )
        if status == "added":
            added += 1
            added_activities.append(activity)
        elif status == "updated":
            updated += 1

    db.flush()
    added_ids = [a.id for a in added_activities]
    db.commit()

    if updated:
        rebuild_groups(db)
    elif added_ids:
        if len(added_ids) > 20:
            rebuild_groups(db)
        else:
            incremental_rebuild_groups(db, added_ids)
    return local_redirect(request, "/manage")


# ---------------- Garmin Connect auto-sync (unofficial) ----------------

def do_garmin_sync(db: Session):
    """Shared by the manual 'Sync from Garmin' button and the background
    loop. Returns (added, updated). Raises GarminAuthError on login failure."""
    client = garmin_client.get_client()

    sync_state = db.query(GarminSyncState).first()
    last_checked = sync_state.last_checked_at if sync_state else None

    new_activities = garmin_client.fetch_new_activities(client, after=last_checked)

    added, updated = 0, 0
    added_activities = []
    newest_seen = last_checked
    for a in new_activities:
        activity_id = a.get("activityId")
        if not activity_id:
            continue

        # Advance the watermark for every activity we see, whether or not it
        # ends up importable, so ones with no GPS data (strength training,
        # indoor workouts, etc.) aren't retried on every future sync.
        start_str = a.get("startTimeLocal") or a.get("startTimeGMT")
        if start_str:
            try:
                start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
                if newest_seen is None or start_dt > newest_seen:
                    newest_seen = start_dt
            except ValueError:
                pass

        try:
            gpx_bytes = garmin_client.download_gpx(client, activity_id)
        except Exception as e:
            logger.warning("Failed to download GPX for Garmin activity %s: %s", activity_id, e)
            continue
        try:
            parsed = parse_gpx_bytes(gpx_bytes, fallback_name=str(activity_id))
        except Exception as e:
            logger.warning("Failed to parse GPX for Garmin activity %s: %s", activity_id, e)
            continue

        # Garmin Connect's own activity metadata is richer/more accurate
        # than what's recoverable from the raw GPX (e.g. your actual title).
        name = a.get("activityName") or parsed["name"]
        activity_type = None
        type_key = (a.get("activityType") or {}).get("typeKey")
        if type_key:
            activity_type = type_key.replace("_", " ").title()
        distance_m = a.get("distance") or parsed["distance_m"]
        duration_s = a.get("duration") or parsed["duration_s"]

        status, activity = _save_activity(
            db, source="garmin", external_id=str(activity_id),
            name=name, points=parsed["points"], distance_m=distance_m,
            duration_s=duration_s, start_time=parsed["start_time"],
            activity_type=activity_type,
        )
        if status == "added":
            added += 1
            added_activities.append(activity)
        elif status == "updated":
            updated += 1

    if sync_state is None:
        sync_state = GarminSyncState(last_checked_at=newest_seen)
        db.add(sync_state)
    else:
        sync_state.last_checked_at = newest_seen

    db.flush()  # assign primary keys to newly added activities before reading their ids
    added_ids = [a.id for a in added_activities]
    db.commit()

    if updated:
        rebuild_groups(db)
    elif added_ids:
        if len(added_ids) > 20:
            # A big catch-up sync (e.g. the very first run after connecting,
            # pulling in a lot of history at once) - not meaningfully
            # cheaper to do incrementally than just rebuilding fully.
            rebuild_groups(db)
        else:
            # The routine case: a scheduled sync bringing in a handful of
            # new activities. This is the specific path that used to pay a
            # full O(n^2) recompute cost on every single background check -
            # now it only compares the new activities against what's
            # relevant instead of re-deriving the whole history.
            incremental_rebuild_groups(db, added_ids)
    return added, updated


@app.post("/garmin/sync")
def garmin_sync_route(request: Request, db: Session = Depends(get_db)):
    try:
        added, updated = do_garmin_sync(db)
    except garmin_client.GarminAuthError as e:
        prefix = ingress_prefix(request)
        return HTMLResponse(f"<p>{e}</p><p><a href='{prefix}/'>back</a></p>", status_code=401)
    return local_redirect(request, f"/manage?imported={added}&updated={updated}&skipped=0&duplicates=0&unsupported=0")


@app.get("/garmin/login", response_class=HTMLResponse)
def garmin_login_page(request: Request):
    return templates.TemplateResponse("garmin_login.html", {
        "request": request, "step": "credentials",
        "default_email": garmin_client.GARMIN_EMAIL,
    })


@app.post("/garmin/login/start")
def garmin_login_start(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        status, token = garmin_client.start_web_login(email, password)
    except garmin_client.GarminAuthError as e:
        return templates.TemplateResponse("garmin_login.html", {
            "request": request, "step": "credentials", "default_email": email, "error": str(e),
        })
    if status == "needs_mfa":
        return templates.TemplateResponse("garmin_login.html", {
            "request": request, "step": "mfa", "mfa_token": token,
        })
    return local_redirect(request, "/manage")


@app.post("/garmin/login/mfa")
def garmin_login_mfa(request: Request, mfa_token: str = Form(...), mfa_code: str = Form(...)):
    try:
        garmin_client.complete_web_login(mfa_token, mfa_code)
    except garmin_client.GarminAuthError as e:
        return templates.TemplateResponse("garmin_login.html", {
            "request": request, "step": "mfa", "mfa_token": mfa_token, "error": str(e),
        })
    return local_redirect(request, "/manage")


async def _garmin_background_sync_loop():
    while True:
        await asyncio.sleep(GARMIN_SYNC_INTERVAL_MINUTES * 60)
        if not (garmin_client.is_configured() or garmin_client.has_saved_session()):
            logger.info("Background Garmin sync: skipped scheduled check (not configured, no saved session)")
            continue
        logger.info("Background Garmin sync: starting scheduled check")
        db = SessionLocal()
        try:
            added, updated = do_garmin_sync(db)
            logger.info(
                "Background Garmin sync: scheduled check complete - %d added, %d updated",
                added, updated,
            )
        except garmin_client.GarminAuthError as e:
            logger.warning("Background Garmin sync: scheduled check failed (auth error): %s", e)
        except Exception as e:
            logger.warning("Background Garmin sync: scheduled check failed: %s", e)
        finally:
            db.close()


@app.on_event("startup")
async def _start_background_tasks():
    if garmin_client.is_configured() or garmin_client.has_saved_session():
        logger.info(
            "Garmin auto-sync enabled, running every %d minutes.",
            GARMIN_SYNC_INTERVAL_MINUTES,
        )
        asyncio.create_task(_garmin_background_sync_loop())
