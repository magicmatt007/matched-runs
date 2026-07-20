import json
import time
import os
import gzip
import logging
import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, Request, UploadFile, Depends
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
from app.matcher import resample_track, rebuild_groups, find_cross_source_duplicate, merge_duplicate_activities, SOURCE_PRIORITY
from app.polyline_util import decode_polyline
from app import strava_client
from app import garmin_client

logger = logging.getLogger("matched_runs")
logging.basicConfig(level=logging.INFO)

GARMIN_SYNC_INTERVAL_MINUTES = int(os.environ.get("GARMIN_SYNC_INTERVAL_MINUTES", 120))

Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(title="Matched Runs")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _save_activity(db: Session, source: str, external_id: str, name: str,
                    points, distance_m: float, duration_s, start_time,
                    activity_type: str = None):
    """Returns ("added", activity), ("updated", activity), or ("unchanged", activity)."""
    activity_type = activity_type or "Other"
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


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
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

    return templates.TemplateResponse("index.html", {
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

        status, activity = _save_activity(
            db, source=source, external_id=f.filename,
            name=parsed["name"], points=parsed["points"],
            distance_m=parsed["distance_m"], duration_s=parsed["duration_s"],
            start_time=parsed["start_time"], activity_type=parsed.get("activity_type"),
        )
        if status == "added":
            added += 1
        elif status == "updated":
            updated += 1
        else:
            unchanged += 1

    db.commit()
    if added or updated:
        rebuild_groups(db)

    logger.info(
        "Upload finished: %s added, %s updated, %s unchanged, %s skipped (%s unsupported extension). Extensions seen: %s",
        added, updated, unchanged, skipped, unsupported_ext, sorted(seen_extensions),
    )
    return RedirectResponse(
        f"/?imported={added}&updated={updated}&skipped={skipped}"
        f"&duplicates={unchanged}&unsupported={unsupported_ext}",
        status_code=303,
    )


@app.get("/activities", response_class=HTMLResponse)
def activities_list(request: Request, type: str = None, db: Session = Depends(get_db)):
    query = db.query(Activity).order_by(Activity.start_time.desc())
    if type:
        query = query.filter(Activity.activity_type == type)
    activities = query.all()

    types = sorted({
        row[0] for row in db.query(Activity.activity_type).distinct().all() if row[0]
    })

    return templates.TemplateResponse("activities.html", {
        "request": request,
        "activities": activities,
        "types": types,
        "selected_type": type,
    })


@app.get("/group/{group_id}", response_class=HTMLResponse)
def group_detail(group_id: int, request: Request, db: Session = Depends(get_db)):
    group = db.query(RouteGroup).filter_by(id=group_id).first()
    return templates.TemplateResponse("group.html", {"request": request, "group": group})


@app.get("/activity/{activity_id}", response_class=HTMLResponse)
def activity_detail(activity_id: int, request: Request, db: Session = Depends(get_db)):
    activity = db.query(Activity).filter_by(id=activity_id).first()
    return templates.TemplateResponse("activity.html", {"request": request, "activity": activity})


@app.post("/dedupe")
def dedupe_route(db: Session = Depends(get_db)):
    removed = merge_duplicate_activities(db)
    logger.info("Deduplication: merged/removed %d duplicate activities", removed)
    return RedirectResponse(
        f"/?imported=0&updated={removed}&skipped=0&duplicates=0&unsupported=0",
        status_code=303,
    )


@app.post("/rebuild")
def rebuild(db: Session = Depends(get_db)):
    rebuild_groups(db)
    return RedirectResponse("/", status_code=303)


# ---------------- Strava OAuth + sync ----------------

@app.get("/strava/login")
def strava_login():
    if not strava_client.is_configured():
        return HTMLResponse(
            "Strava API not configured. Set STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET in .env", status_code=400
        )
    return RedirectResponse(strava_client.get_authorize_url())


@app.get("/strava/callback")
def strava_callback(code: str = None, error: str = None, scope: str = None, db: Session = Depends(get_db)):
    if error or not code:
        return RedirectResponse("/", status_code=303)

    data = strava_client.exchange_code_for_token(code)

    if not scope or "activity:read_all" not in scope:
        return HTMLResponse(
            "<p>Strava did not grant the <code>activity:read_all</code> permission "
            f"(granted scope: <code>{scope}</code>).</p>"
            "<p>This usually happens when this app was authorized before with a "
            "narrower scope, and Strava is silently reusing that old grant. Fix: "
            "go to <a href='https://www.strava.com/settings/apps' target='_blank'>"
            "strava.com/settings/apps</a>, revoke access for this app, then "
            "<a href='/strava/login'>connect again</a>.</p>",
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
    return RedirectResponse("/", status_code=303)


@app.post("/strava/disconnect")
def strava_disconnect(db: Session = Depends(get_db)):
    db.query(StravaToken).delete()
    db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/strava/sync")
def strava_sync(db: Session = Depends(get_db)):
    token = db.query(StravaToken).first()
    if not token:
        return RedirectResponse("/", status_code=303)

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
        return HTMLResponse(
            f"<p>{e}</p>"
            "<p>Your stored Strava connection was cleared. Please "
            "<a href='/strava/login'>connect Strava</a> again — if this keeps "
            "happening, also revoke the app at "
            "<a href='https://www.strava.com/settings/apps' target='_blank'>"
            "strava.com/settings/apps</a> first, then reconnect.</p>"
            "<p><a href='/'>back</a></p>",
            status_code=401,
        )

    added, updated = 0, 0
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
        elif status == "updated":
            updated += 1
    db.commit()
    if added or updated:
        rebuild_groups(db)
    return RedirectResponse("/", status_code=303)


# ---------------- Garmin Connect auto-sync (unofficial) ----------------

def do_garmin_sync(db: Session):
    """Shared by the manual 'Sync from Garmin' button and the background
    loop. Returns (added, updated). Raises GarminAuthError on login failure."""
    client = garmin_client.get_client()

    sync_state = db.query(GarminSyncState).first()
    last_checked = sync_state.last_checked_at if sync_state else None

    new_activities = garmin_client.fetch_new_activities(client, after=last_checked)

    added, updated = 0, 0
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

        status, _ = _save_activity(
            db, source="garmin", external_id=str(activity_id),
            name=name, points=parsed["points"], distance_m=distance_m,
            duration_s=duration_s, start_time=parsed["start_time"],
            activity_type=activity_type,
        )
        if status == "added":
            added += 1
        elif status == "updated":
            updated += 1

    if sync_state is None:
        sync_state = GarminSyncState(last_checked_at=newest_seen)
        db.add(sync_state)
    else:
        sync_state.last_checked_at = newest_seen

    db.commit()
    if added or updated:
        rebuild_groups(db)
    return added, updated


@app.post("/garmin/sync")
def garmin_sync_route(db: Session = Depends(get_db)):
    try:
        added, updated = do_garmin_sync(db)
    except garmin_client.GarminAuthError as e:
        return HTMLResponse(f"<p>{e}</p><p><a href='/'>back</a></p>", status_code=401)
    return RedirectResponse(
        f"/?imported={added}&updated={updated}&skipped=0&duplicates=0&unsupported=0",
        status_code=303,
    )


async def _garmin_background_sync_loop():
    while True:
        await asyncio.sleep(GARMIN_SYNC_INTERVAL_MINUTES * 60)
        if not garmin_client.is_configured():
            continue
        db = SessionLocal()
        try:
            added, updated = do_garmin_sync(db)
            if added or updated:
                logger.info("Background Garmin sync: %d added, %d updated", added, updated)
        except garmin_client.GarminAuthError as e:
            logger.warning("Background Garmin sync auth failed: %s", e)
        except Exception as e:
            logger.warning("Background Garmin sync failed: %s", e)
        finally:
            db.close()


@app.on_event("startup")
async def _start_background_tasks():
    if garmin_client.is_configured():
        logger.info(
            "Garmin auto-sync enabled, running every %d minutes.",
            GARMIN_SYNC_INTERVAL_MINUTES,
        )
        asyncio.create_task(_garmin_background_sync_loop())
