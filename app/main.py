import json
import time
import os
import gzip
import logging
import asyncio
import sqlite3
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, UploadFile, Depends, Form, File
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import Base, engine, get_db, run_migrations, SessionLocal, DB_PATH
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


# Single global import job state - fine for a personal, single-user app
# (no need to track multiple concurrent jobs). Runs on a background thread
# via asyncio.to_thread so it survives the request completing (and the
# browser navigating away), and so the CPU-bound parsing/matching work
# doesn't block the event loop from serving other requests - importantly,
# the progress-polling endpoint itself needs to stay responsive *while*
# this is running.
_current_import_job = {"active": False}

# Python's own asyncio.create_task() docs warn that a task can be garbage
# collected mid-execution if nothing keeps a reference to it - this holds
# one so that doesn't happen to a multi-minute import or the Garmin sync
# loop.
_background_tasks = set()


def _spawn_background_task(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _run_import_sync(files_data, name_overrides, job):
    """The actual (slow, synchronous) import work: parse every file, save
    each activity, then re-run route matching. Runs on a worker thread via
    asyncio.to_thread - deliberately kept as plain synchronous code with no
    async/await, since there's no natural yield point in the middle of
    parsing a file or running the O(n^2) matching loop anyway."""
    db = SessionLocal()
    try:
        added, updated, unchanged, skipped, unsupported_ext = 0, 0, 0, 0, 0
        seen_extensions = set()
        added_activities = []

        for filename, content in files_data:
            job["processed"] += 1
            lower_name = filename.lower()

            if lower_name.endswith(".gz"):
                try:
                    content = gzip.decompress(content)
                except Exception as e:
                    logger.warning("Failed to gunzip %s: %s", filename, e)
                    skipped += 1
                    continue
                lower_name = lower_name[:-3]

            ext = lower_name.rsplit(".", 1)[-1] if "." in lower_name else "(none)"
            seen_extensions.add(ext)

            try:
                if lower_name.endswith(".fit"):
                    parsed = parse_fit_bytes(content, fallback_name=filename)
                elif lower_name.endswith(".gpx"):
                    parsed = parse_gpx_bytes(content, fallback_name=filename)
                elif lower_name.endswith(".tcx"):
                    parsed = parse_tcx_bytes(content, fallback_name=filename)
                else:
                    unsupported_ext += 1
                    skipped += 1
                    continue
            except Exception as e:
                logger.warning("Failed to parse %s: %s", filename, e)
                skipped += 1
                continue

            if lower_name.endswith(".gpx"):
                source = "gpx"
            elif lower_name.endswith(".tcx"):
                source = "tcx"
            else:
                source = "fit"

            base_filename = filename.rsplit("/", 1)[-1].strip().lower()
            final_name = name_overrides.get(base_filename) or parsed["name"]

            status, activity = _save_activity(
                db, source=source, external_id=filename,
                name=final_name, points=parsed["points"],
                distance_m=parsed["distance_m"], duration_s=parsed["duration_s"],
                start_time=parsed["start_time"], activity_type=parsed.get("activity_type"),
                elevation_gain_m=parsed.get("elevation_gain_m"),
                elevation_loss_m=parsed.get("elevation_loss_m"),
                avg_heart_rate=parsed.get("avg_heart_rate"),
                max_heart_rate=parsed.get("max_heart_rate"),
                avg_cadence=parsed.get("avg_cadence"),
                calories=parsed.get("calories"),
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

        db.flush()
        added_ids = [a.id for a in added_activities]
        db.commit()

        job["phase"] = "matching"
        job["match_done"] = 0
        job["match_total"] = 0

        def report_match_progress(done, total):
            job["match_done"] = done
            job["match_total"] = total

        if updated:
            # A metadata-only update (name/type backfill) doesn't need
            # re-matching, but a cross-source-duplicate merge DOES change an
            # activity's geometry - play it safe with a full rebuild
            # whenever any update happened at all.
            rebuild_groups(db, progress_callback=report_match_progress)
        elif added_ids:
            if len(added_ids) > 20:
                # A big bulk import has enough new activities that
                # comparing each one against everything isn't meaningfully
                # cheaper than a full rebuild.
                rebuild_groups(db, progress_callback=report_match_progress)
            else:
                incremental_rebuild_groups(db, added_ids)

        logger.info(
            "Upload finished: %s added, %s updated, %s unchanged, %s skipped (%s unsupported extension). Extensions seen: %s",
            added, updated, unchanged, skipped, unsupported_ext, sorted(seen_extensions),
        )

        job["phase"] = "done"
        job["added"] = added
        job["updated"] = updated
        job["unchanged"] = unchanged
        job["skipped"] = skipped
        job["unsupported"] = unsupported_ext
    except Exception as e:
        logger.exception("Import job failed")
        job["phase"] = "error"
        job["error"] = str(e)
    finally:
        job["active"] = False
        db.close()


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
                    activity_type: str = None, elevation_gain_m=None,
                    elevation_loss_m=None, avg_heart_rate=None,
                    max_heart_rate=None, avg_cadence=None, calories=None):
    """Returns ("added", activity), ("updated", activity), or ("unchanged", activity)."""
    activity_type = normalize_activity_type(activity_type or "Other")
    existing = db.query(Activity).filter_by(source=source, external_id=external_id).first()

    extra_fields = {
        "elevation_gain_m": elevation_gain_m,
        "elevation_loss_m": elevation_loss_m,
        "avg_heart_rate": avg_heart_rate,
        "max_heart_rate": max_heart_rate,
        "avg_cadence": avg_cadence,
        "calories": calories,
    }

    if existing:
        # Re-imported (e.g. re-uploading the same export after an app update
        # added new fields, such as activity_type or these newer ones).
        # Backfill in place rather than silently skipping, so re-uploading
        # is enough to pick up new fields without needing to wipe and
        # reimport everything.
        changed = False
        if existing.activity_type != activity_type:
            existing.activity_type = activity_type
            changed = True
        if name and existing.name != name:
            existing.name = name
            changed = True
        for field, value in extra_fields.items():
            if value is not None and getattr(existing, field) != value:
                setattr(existing, field, value)
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
            for field, value in extra_fields.items():
                if value is not None:
                    setattr(dup, field, value)
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
        **extra_fields,
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
        "import_job": _current_import_job,
        "garmin_sync_job": _current_garmin_sync_job,
        "db_import_job": _current_db_import_job,
    })


@app.get("/manage/import-status")
def import_status():
    """Polled by the Import & Sync page's progress bar - plain JSON, no
    template needed."""
    return _current_import_job


@app.post("/upload")
async def upload_gpx(request: Request):
    if _current_import_job.get("active"):
        prefix = ingress_prefix(request)
        return HTMLResponse(
            "<p>An import is already running. Check the Import &amp; Sync "
            "page for its progress, and wait for it to finish before "
            "starting another.</p>"
            f"<p><a href='{prefix}/manage'>back</a></p>",
            status_code=409,
        )

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

    # Read every file's bytes now, while the request/upload stream is still
    # available - the actual (slow) parsing happens afterward on a
    # background thread, by which point these UploadFile objects can no
    # longer be read from.
    name_overrides = {}
    files_data = []  # list of (filename, content_bytes)
    for f in files:
        if not (hasattr(f, "filename") and hasattr(f, "read")):
            logger.warning("Skipping non-file form field under 'files': %r", f)
            continue
        filename = f.filename or ""
        content = await f.read()
        if filename.lower().endswith("activities.csv"):
            try:
                found = parse_strava_activities_csv(content)
                name_overrides.update(found)
                logger.info("Loaded %d titles from %s", len(found), filename)
            except Exception as e:
                logger.warning("Failed to parse %s: %s", filename, e)
        else:
            files_data.append((filename, content))

    if not files_data and not name_overrides:
        return local_redirect(request, "/manage?imported=0&updated=0&skipped=0&duplicates=0&unsupported=0")

    _current_import_job.clear()
    _current_import_job.update({
        "active": True,
        "phase": "importing",
        "processed": 0,
        "total": len(files_data),
        "match_done": 0,
        "match_total": 0,
        "started_at": time.time(),
        "added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "unsupported": 0,
        "error": None,
    })

    # Runs on a worker thread (see _run_import_sync's docstring for why),
    # as a fire-and-forget task the request doesn't wait on - so it keeps
    # running to completion regardless of whether the browser navigates
    # away or even closes entirely.
    _spawn_background_task(asyncio.to_thread(_run_import_sync, files_data, name_overrides, _current_import_job))

    return local_redirect(request, "/manage")


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


def _ordered_activity_ids_for_context(db: Session, request: Request):
    """Reconstructs the same ordered list of activity ids that whichever
    list page linked to this activity was showing, so Prev/Next navigation
    reflects the exact sort/filter/context that was active - not some
    arbitrary global order. Returns None if the request has no recognized
    list context (e.g. reached directly via a bookmark)."""
    list_type = request.query_params.get("list")

    if list_type == "activities":
        query = db.query(Activity.id)
        type_filter = request.query_params.get("type")
        if type_filter:
            query = query.filter(Activity.activity_type == type_filter)
        query, _, _ = apply_sort_and_filters(query, request)
        return [row[0] for row in query.all()]

    if list_type == "group":
        try:
            group_id = int(request.query_params.get("group_id", ""))
        except ValueError:
            return None
        query = db.query(Activity.id).filter(Activity.group_id == group_id)
        query, _, _ = apply_sort_and_filters(query, request)
        return [row[0] for row in query.all()]

    if list_type == "log":
        log_type = request.query_params.get("log_type")
        period = request.query_params.get("period", "7d")
        if period not in ("7d", "4w", "1y"):
            period = "7d"
        try:
            offset = int(request.query_params.get("offset", "0"))
        except ValueError:
            offset = 0
        month = request.query_params.get("month")

        query = db.query(Activity)
        if log_type:
            query = query.filter(Activity.activity_type == log_type)

        drill_month = None
        if period == "1y" and month:
            try:
                drill_month = datetime.strptime(month, "%Y-%m")
            except ValueError:
                drill_month = None

        if drill_month:
            month_start = drill_month.replace(day=1)
            month_next = _add_months(month_start, 1)
            window_start, window_end = month_start, month_next - timedelta(seconds=1)
        elif period == "1y":
            window_start, window_end, _ = _year_window(offset)
        else:
            window_start, window_end, _ = _period_window(period, offset)

        rows = (
            query.filter(Activity.start_time >= window_start, Activity.start_time <= window_end)
            .order_by(Activity.start_time.desc())
            .with_entities(Activity.id)
            .all()
        )
        return [row[0] for row in rows]

    return None


@app.get("/activity/{activity_id}", response_class=HTMLResponse)
def activity_detail(activity_id: int, request: Request, db: Session = Depends(get_db)):
    activity = db.query(Activity).filter_by(id=activity_id).first()

    prev_id = next_id = None
    list_ids = _ordered_activity_ids_for_context(db, request)
    if list_ids and activity_id in list_ids:
        idx = list_ids.index(activity_id)
        if idx > 0:
            prev_id = list_ids[idx - 1]
        if idx < len(list_ids) - 1:
            next_id = list_ids[idx + 1]

    return templates.TemplateResponse("activity.html", {
        "request": request,
        "activity": activity,
        "prev_id": prev_id,
        "next_id": next_id,
        # Carried forward on the Prev/Next links themselves so continued
        # navigation keeps working, not just the initial link into here.
        "nav_query_string": request.url.query,
    })


# ---------------- Training log (period-based view, grouped by month for 1y) ----------------

PERIOD_DAYS = {"7d": 7, "4w": 28}  # 1y is handled separately - see _year_window


def _period_window(period: str, offset: int):
    """Returns (window_start, window_end, span_days) for a rolling period
    ending `offset` periods ago (offset=0 -> ending today). Used for 7d/4w
    only - the 1y view uses _year_window instead (calendar-month aligned,
    not a rolling day count)."""
    days = PERIOD_DAYS.get(period, 7)
    today = datetime.utcnow().date()
    end_date = today - timedelta(days=offset * days)
    start_date = end_date - timedelta(days=days - 1)
    window_start = datetime.combine(start_date, datetime.min.time())
    window_end = datetime.combine(end_date, datetime.max.time())
    return window_start, window_end, days


def _add_months(dt: datetime, delta_months: int) -> datetime:
    """Add (or subtract) whole calendar months, clamped to day=1 - only
    ever used here for month boundaries, never a specific day-of-month."""
    total = dt.year * 12 + (dt.month - 1) + delta_months
    year = total // 12
    month = total % 12 + 1
    return dt.replace(year=year, month=month, day=1)


def _year_window(offset: int):
    """12 calendar months ending with the CURRENT month (offset=0), e.g. on
    any day in July 2026, that's August 2025 through July 2026, including
    the still-in-progress July. offset=1 shifts the whole window back by
    12 months, etc."""
    today = datetime.utcnow().date()
    end_month_start = _add_months(datetime(today.year, today.month, 1), -12 * offset)
    start_month_start = _add_months(end_month_start, -11)
    window_start = start_month_start
    window_end = _add_months(end_month_start, 1) - timedelta(seconds=1)
    span_days = (window_end - window_start).days + 1
    return window_start, window_end, span_days


@app.get("/log", response_class=HTMLResponse)
def training_log(request: Request, type: str = None, period: str = "7d",
                  offset: int = 0, month: str = None, db: Session = Depends(get_db)):
    if period not in ("7d", "4w", "1y"):
        period = "7d"
    if offset < 0:
        offset = 0

    types = sorted({
        row[0] for row in db.query(Activity.activity_type).distinct().all() if row[0]
    })
    if not type:
        if "Running" in types:
            type = "Running"
        elif types:
            type = types[0]

    base_query = db.query(Activity)
    if type:
        base_query = base_query.filter(Activity.activity_type == type)

    today = datetime.utcnow().date()

    # Drilling into a specific month only applies within the 1y view.
    drill_month = None
    if period == "1y" and month:
        try:
            drill_month = datetime.strptime(month, "%Y-%m")
        except ValueError:
            drill_month = None

    current_month = prev_month = next_month = None

    if drill_month:
        month_start = drill_month.replace(day=1)
        month_next = _add_months(month_start, 1)
        window_start = month_start
        window_end = month_next - timedelta(seconds=1)
        span_days = (month_next - month_start).days
        current_month = month_start.strftime("%Y-%m")
        prev_month = (month_start - timedelta(days=1)).strftime("%Y-%m")
        if month_next.date() <= today:
            next_month = month_next.strftime("%Y-%m")
        mode = "list"
    elif period == "1y":
        window_start, window_end, span_days = _year_window(offset)
        mode = "monthly"
    else:
        window_start, window_end, span_days = _period_window(period, offset)
        mode = "list"

    activities = (
        base_query.filter(Activity.start_time >= window_start, Activity.start_time <= window_end)
        .order_by(Activity.start_time.desc())
        .all()
    )

    month_groups = []
    if mode == "monthly":
        groups = {}
        for a in activities:
            key = a.start_time.strftime("%Y-%m")
            g = groups.setdefault(key, {
                "month": key,
                "label": a.start_time.strftime("%B %Y"),
                "chart_label": a.start_time.strftime("%b"),
                "count": 0,
                "distance_m": 0.0,
            })
            g["count"] += 1
            g["distance_m"] += a.distance_m or 0
        # Make sure every month in the 12-month window appears (even ones
        # with zero activities), so the chart below has a complete axis.
        cursor = window_start
        while cursor <= window_end:
            key = cursor.strftime("%Y-%m")
            groups.setdefault(key, {
                "month": key, "label": cursor.strftime("%B %Y"),
                "chart_label": cursor.strftime("%b"), "count": 0, "distance_m": 0.0,
            })
            cursor = _add_months(cursor, 1)
        month_groups = sorted(groups.values(), key=lambda g: g["month"], reverse=True)

    # Chart data: always chronological (oldest to newest), independent of
    # the table/card display order above. Monthly buckets for the 1y
    # overview (using the short 3-letter month label - any 12 consecutive
    # calendar months always contain each month name exactly once, so this
    # stays unambiguous without needing a year suffix), daily buckets
    # otherwise (7d, 4w, and month drill-down all get a sensible
    # day-by-day axis this way, without special-casing drill-down
    # separately).
    if mode == "monthly":
        chart_data = [
            {"label": g["chart_label"], "distance_m": g["distance_m"]}
            for g in sorted(month_groups, key=lambda g: g["month"])
        ]
    else:
        by_day = {}
        for a in activities:
            if a.start_time:
                d = a.start_time.date()
                by_day[d] = by_day.get(d, 0.0) + (a.distance_m or 0)
        chart_data = []
        cursor = window_start.date()
        end_date = window_end.date()
        while cursor <= end_date:
            chart_data.append({"label": cursor.strftime("%b %d"), "distance_m": by_day.get(cursor, 0.0)})
            cursor += timedelta(days=1)

    total_distance_km = sum(a.distance_m or 0 for a in activities) / 1000.0
    avg_weekly_km = (total_distance_km / span_days * 7) if span_days else 0.0
    avg_monthly_km = (total_distance_km / span_days * 30.44) if span_days else 0.0

    return templates.TemplateResponse("log.html", {
        "request": request,
        "types": types,
        "selected_type": type,
        "period": period,
        "offset": offset,
        "mode": mode,
        "activities": activities,
        "month_groups": month_groups,
        "chart_data": chart_data,
        "current_month": current_month,
        "prev_month": prev_month,
        "next_month": next_month,
        "window_start": window_start,
        "window_end": window_end,
        "total_distance_km": total_distance_km,
        "avg_weekly_km": avg_weekly_km,
        "avg_monthly_km": avg_monthly_km,
        "can_go_next_period": offset > 0,
    })


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


# ---------------- Database export/import ----------------
# Handy for running the (potentially slow, especially on something like a
# Raspberry Pi) initial bulk import and route matching on a more powerful
# machine, then moving the already-populated database over to wherever
# you'll actually run the app day to day.

_current_db_import_job = {"active": False}


def _run_db_import_sync(content: bytes, job):
    """Runs on a background thread - same reasoning as _run_import_sync:
    a 100MB+ database write can genuinely take a while on slow storage
    (e.g. a Raspberry Pi's SD card), and this keeps the progress-polling
    endpoint responsive throughout, and keeps running if you navigate away."""
    t_start = time.time()
    logger.info("[db-import] background job starting with %d bytes in hand", len(content))
    try:
        if not content.startswith(b"SQLite format 3\x00"):
            job["phase"] = "error"
            job["error"] = "That doesn't look like a valid SQLite database file."
            return

        # Write in chunks (rather than one f.write(content) call) so we can
        # report real byte-level progress - this is usually the slowest
        # part for a large file on slow storage, unlike the validation/
        # swap/migration steps below which are fast regardless of file size.
        job["phase"] = "writing"
        job["total"] = len(content)
        job["processed"] = 0
        tmp_path = DB_PATH + ".importing"
        chunk_size = 1024 * 1024  # 1MB
        with open(tmp_path, "wb") as f:
            for offset in range(0, len(content), chunk_size):
                chunk = content[offset:offset + chunk_size]
                f.write(chunk)
                job["processed"] += len(chunk)
        t_write_done = time.time()
        logger.info("[db-import] wrote %d bytes to disk in %.2fs", len(content), t_write_done - t_start)

        job["phase"] = "validating"
        try:
            check = sqlite3.connect(tmp_path)
            tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            check.close()
        except Exception as e:
            os.remove(tmp_path)
            job["phase"] = "error"
            job["error"] = f"Couldn't read that file as a SQLite database: {e}"
            return
        t_validate_done = time.time()
        logger.info("[db-import] validated in %.2fs", t_validate_done - t_write_done)

        if "activities" not in tables:
            os.remove(tmp_path)
            job["phase"] = "error"
            job["error"] = "That SQLite file doesn't look like a Matched Runs database (missing the expected tables)."
            return

        # Close existing connections before swapping the file out from
        # under them - SQLAlchemy will open fresh ones against the new
        # file on the next query.
        job["phase"] = "replacing"
        engine.dispose()
        os.replace(tmp_path, DB_PATH)
        t_replace_done = time.time()
        logger.info("[db-import] replaced database file in %.2fs", t_replace_done - t_validate_done)

        # In case the imported database predates a schema change (e.g. it
        # came from a slightly older version of the app), backfill any new
        # columns.
        job["phase"] = "migrating"
        run_migrations()
        t_migrate_done = time.time()
        logger.info("[db-import] ran migrations in %.2fs", t_migrate_done - t_replace_done)

        logger.info(
            "[db-import] background job finished, total %.2fs (write=%.2fs validate=%.2fs replace=%.2fs migrate=%.2fs)",
            t_migrate_done - t_start,
            t_write_done - t_start,
            t_validate_done - t_write_done,
            t_replace_done - t_validate_done,
            t_migrate_done - t_replace_done,
        )
        job["phase"] = "done"
    except Exception as e:
        logger.exception("Database import job failed")
        job["phase"] = "error"
        job["error"] = str(e)
    finally:
        job["active"] = False


@app.get("/manage/export-db")
def export_db():
    # Use SQLite's own backup API rather than just handing back the raw
    # file - a plain file copy could catch the database mid-write (or miss
    # data sitting in a separate -wal file if write-ahead logging is on),
    # producing a corrupt or incomplete export. The backup API produces a
    # consistent snapshot regardless.
    export_path = "/tmp/matched-runs-export.db"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(export_path)
    src.backup(dst)
    dst.close()
    src.close()
    return FileResponse(
        export_path,
        filename="matched-runs-export.db",
        media_type="application/octet-stream",
    )


def _extract_boundary(content_type: str):
    if not content_type or "boundary=" not in content_type:
        return None
    boundary = content_type.split("boundary=", 1)[1]
    boundary = boundary.split(";", 1)[0].strip().strip('"')
    return boundary.encode()


def _extract_single_file_part(raw_body: bytes, boundary: bytes):
    """Extracts the content of the (single) file part from a simple
    multipart/form-data body with exactly one file field - not a general
    RFC2046 multipart parser, just enough for our own upload form's shape
    (one file input, nothing else). Verified against realistic
    browser-shaped multipart bodies, including binary content containing
    embedded CRLF sequences, before relying on it here."""
    marker = b"--" + boundary
    parts = raw_body.split(marker)
    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_blob = part[:header_end]
        body_blob = part[header_end + 4:]
        if b"filename=" in headers_blob:
            if body_blob.endswith(b"\r\n"):
                body_blob = body_blob[:-2]
            return body_blob
    return None


@app.post("/manage/import-db")
async def import_db(request: Request):
    if _current_db_import_job.get("active"):
        prefix = ingress_prefix(request)
        return HTMLResponse(
            "<p>A database import is already running. Check the Import "
            "&amp; Sync page for its progress.</p>"
            f"<p><a href='{prefix}/manage'>back</a></p>",
            status_code=409,
        )

    t_request_start = time.time()
    logger.info("[db-import] request received")

    boundary = _extract_boundary(request.headers.get("content-type", ""))
    if not boundary:
        prefix = ingress_prefix(request)
        return HTMLResponse(
            "<p>Invalid upload request (missing multipart boundary).</p>"
            f"<p><a href='{prefix}/manage'>back</a></p>",
            status_code=400,
        )

    # The declared Content-Length is the whole multipart body (slightly
    # larger than the file itself, due to boundaries/headers) - close
    # enough for a progress percentage/ETA, corrected to the exact byte
    # count once reading finishes below.
    try:
        declared_total = int(request.headers.get("content-length", "0"))
    except ValueError:
        declared_total = 0

    _current_db_import_job.clear()
    _current_db_import_job.update({
        "active": True,
        "phase": "receiving",
        "processed": 0,
        "total": declared_total,
        "started_at": t_request_start,
        "error": None,
    })

    # Deliberately NOT using UploadFile = File(...) here - FastAPI/Starlette
    # fully consumes and parses the entire multipart body as part of
    # resolving that dependency, BEFORE this function body even starts
    # running. That made every previous attempt at tracking this phase
    # invisible, no matter how the code inside the function was written -
    # the slow part was already over by the time our code got a chance to
    # observe anything. request.stream() instead gives real chunk-by-chunk
    # access as bytes actually arrive over the network, which is what
    # progress here needs to be based on.
    raw_chunks = []
    chunk_count = 0
    async for chunk in request.stream():
        raw_chunks.append(chunk)
        chunk_count += 1
        _current_db_import_job["processed"] += len(chunk)
    t_stream_done = time.time()
    logger.info(
        "[db-import] request.stream() finished: %d chunks, %d bytes, %.2fs (declared content-length was %d)",
        chunk_count, sum(len(c) for c in raw_chunks), t_stream_done - t_request_start, declared_total,
    )

    raw_body = b"".join(raw_chunks)
    t_join_done = time.time()
    logger.info("[db-import] joined chunks into %d bytes in %.2fs", len(raw_body), t_join_done - t_stream_done)
    _current_db_import_job["total"] = len(raw_body)

    content = _extract_single_file_part(raw_body, boundary)
    t_extract_done = time.time()
    logger.info(
        "[db-import] multipart extraction finished in %.2fs, extracted %s bytes",
        t_extract_done - t_join_done, len(content) if content is not None else "None (FAILED)",
    )

    if content is None:
        _current_db_import_job["active"] = False
        _current_db_import_job["phase"] = "error"
        _current_db_import_job["error"] = "Couldn't find the uploaded file in the request."
        prefix = ingress_prefix(request)
        return HTMLResponse(
            "<p>Couldn't find the uploaded file in the request.</p>"
            f"<p><a href='{prefix}/manage'>back</a></p>",
            status_code=400,
        )

    logger.info("[db-import] spawning background job, %.2fs since request started", time.time() - t_request_start)
    _spawn_background_task(asyncio.to_thread(_run_db_import_sync, content, _current_db_import_job))
    return local_redirect(request, "/manage")


@app.get("/manage/db-import-status")
def db_import_status():
    return _current_db_import_job


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
            # Strava's summary activity resource doesn't include a separate
            # elevation "loss" field (only gain), and calories requires a
            # separate per-activity detail API call we're not making here
            # (would multiply the number of requests per sync) - both left
            # as None for Strava-sourced activities.
            elevation_gain_m=a.get("total_elevation_gain"),
            avg_heart_rate=a.get("average_heartrate"),
            max_heart_rate=a.get("max_heartrate"),
            avg_cadence=a.get("average_cadence"),
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

def do_garmin_sync(db: Session, job=None):
    """Shared by the manual 'Sync from Garmin' button and the background
    loop. Returns (added, updated). Raises GarminAuthError on login failure.
    `job`, if given, gets live progress written into it (used by the manual
    button's progress UI; the background auto-sync loop doesn't pass one,
    since there's no UI watching it)."""
    if job is not None:
        job["phase"] = "connecting"
    client = garmin_client.get_client()

    sync_state = db.query(GarminSyncState).first()
    last_checked = sync_state.last_checked_at if sync_state else None

    if job is not None:
        job["phase"] = "fetching"
    new_activities = garmin_client.fetch_new_activities(client, after=last_checked)

    if job is not None:
        job["phase"] = "syncing"
        job["total"] = len(new_activities)
        job["processed"] = 0

    added, updated = 0, 0
    added_activities = []
    newest_seen = last_checked
    for a in new_activities:
        if job is not None:
            job["processed"] += 1
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

        # Prefer Garmin's own device-computed elevation figures; fall back
        # to what we can derive from the downloaded GPX's own elevation
        # points if the API doesn't have them for some reason.
        elevation_gain_m = a.get("elevationGain")
        if elevation_gain_m is None:
            elevation_gain_m = parsed.get("elevation_gain_m")
        elevation_loss_m = a.get("elevationLoss")
        if elevation_loss_m is None:
            elevation_loss_m = parsed.get("elevation_loss_m")

        status, activity = _save_activity(
            db, source="garmin", external_id=str(activity_id),
            name=name, points=parsed["points"], distance_m=distance_m,
            duration_s=duration_s, start_time=parsed["start_time"],
            activity_type=activity_type,
            elevation_gain_m=elevation_gain_m,
            elevation_loss_m=elevation_loss_m,
            avg_heart_rate=a.get("averageHR"),
            max_heart_rate=a.get("maxHR"),
            avg_cadence=a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute"),
            calories=a.get("calories"),
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

    if job is not None:
        job["phase"] = "matching"
        job["match_done"] = 0
        job["match_total"] = 0

        def _report_match_progress(done, total):
            job["match_done"] = done
            job["match_total"] = total

        progress_cb = _report_match_progress
    else:
        progress_cb = None

    if updated:
        rebuild_groups(db, progress_callback=progress_cb)
    elif added_ids:
        if len(added_ids) > 20:
            # A big catch-up sync (e.g. the very first run after connecting,
            # pulling in a lot of history at once) - not meaningfully
            # cheaper to do incrementally than just rebuilding fully.
            rebuild_groups(db, progress_callback=progress_cb)
        else:
            # The routine case: a scheduled sync bringing in a handful of
            # new activities. This is the specific path that used to pay a
            # full O(n^2) recompute cost on every single background check -
            # now it only compares the new activities against what's
            # relevant instead of re-deriving the whole history.
            incremental_rebuild_groups(db, added_ids)
    return added, updated


_current_garmin_sync_job = {"active": False}


def _run_garmin_sync_background(job):
    """Runs the manual 'Sync from Garmin now' button's work on a background
    thread, same reasoning as _run_import_sync - keeps the progress-polling
    endpoint responsive while a slow first sync (or a big catch-up sync)
    runs, and lets it keep going if you navigate away."""
    db = SessionLocal()
    try:
        added, updated = do_garmin_sync(db, job=job)
        job["phase"] = "done"
        job["added"] = added
        job["updated"] = updated
    except garmin_client.GarminAuthError as e:
        job["phase"] = "error"
        job["error"] = str(e)
    except Exception as e:
        logger.exception("Garmin sync job failed")
        job["phase"] = "error"
        job["error"] = str(e)
    finally:
        job["active"] = False
        db.close()


@app.post("/garmin/sync")
async def garmin_sync_route(request: Request):
    if _current_garmin_sync_job.get("active"):
        prefix = ingress_prefix(request)
        return HTMLResponse(
            "<p>A Garmin sync is already running. Check the Import &amp; Sync "
            "page for its progress.</p>"
            f"<p><a href='{prefix}/manage'>back</a></p>",
            status_code=409,
        )
    if not (garmin_client.is_configured() or garmin_client.has_saved_session()):
        return local_redirect(request, "/manage")

    _current_garmin_sync_job.clear()
    _current_garmin_sync_job.update({
        "active": True,
        "phase": "connecting",
        "processed": 0,
        "total": 0,
        "match_done": 0,
        "match_total": 0,
        "started_at": time.time(),
        "added": 0, "updated": 0,
        "error": None,
    })
    _spawn_background_task(asyncio.to_thread(_run_garmin_sync_background, _current_garmin_sync_job))
    return local_redirect(request, "/manage")


@app.get("/manage/garmin-sync-status")
def garmin_sync_status():
    return _current_garmin_sync_job


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
        _spawn_background_task(_garmin_background_sync_loop())
