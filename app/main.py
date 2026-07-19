import json
import time
import os
import gzip
from datetime import datetime, timezone

from fastapi import FastAPI, Request, UploadFile, File, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import Base, engine, get_db, run_migrations
from app.models import Activity, RouteGroup, StravaToken
from app.gpx_parser import parse_gpx_bytes
from app.fit_parser import parse_fit_bytes
from app.matcher import resample_track, rebuild_groups
from app.polyline_util import decode_polyline
from app import strava_client

Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI(title="Matched Runs")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _save_activity(db: Session, source: str, external_id: str, name: str,
                    points, distance_m: float, duration_s, start_time):
    exists = db.query(Activity).filter_by(source=source, external_id=external_id).first()
    if exists:
        return None  # already imported

    resampled = resample_track(points)
    activity = Activity(
        source=source,
        external_id=external_id,
        name=name or "Run",
        start_time=start_time,
        distance_m=distance_m,
        duration_s=duration_s,
        full_points_json=json.dumps(points),
        resampled_points_json=json.dumps(resampled),
    )
    db.add(activity)
    return activity


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

    return templates.TemplateResponse("index.html", {
        "request": request,
        "groups": groups,
        "group_counts": group_counts,
        "ungrouped": ungrouped,
        "total_activities": total_activities,
        "strava_connected": strava_connected,
        "strava_configured": strava_client.is_configured(),
    })


@app.post("/upload")
async def upload_gpx(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    added, skipped = 0, 0
    for f in files:
        content = await f.read()
        lower_name = (f.filename or "").lower()

        # Strava's bulk export gzips some activity files (e.g. "123.gpx.gz").
        if lower_name.endswith(".gz"):
            try:
                content = gzip.decompress(content)
            except Exception:
                skipped += 1
                continue
            lower_name = lower_name[:-3]  # strip trailing ".gz"

        try:
            if lower_name.endswith(".fit"):
                parsed = parse_fit_bytes(content, fallback_name=f.filename)
            elif lower_name.endswith(".gpx"):
                parsed = parse_gpx_bytes(content, fallback_name=f.filename)
            else:
                skipped += 1
                continue
        except Exception:
            # Corrupt file, indoor activity with no GPS, unsupported FIT
            # message layout, etc - skip it and keep processing the rest of
            # the batch rather than aborting the whole import.
            skipped += 1
            continue

        activity = _save_activity(
            db, source="gpx" if lower_name.endswith(".gpx") else "fit", external_id=f.filename,
            name=parsed["name"], points=parsed["points"],
            distance_m=parsed["distance_m"], duration_s=parsed["duration_s"],
            start_time=parsed["start_time"],
        )
        if activity:
            added += 1
    db.commit()
    if added:
        rebuild_groups(db)
    return RedirectResponse("/", status_code=303)


@app.get("/group/{group_id}", response_class=HTMLResponse)
def group_detail(group_id: int, request: Request, db: Session = Depends(get_db)):
    group = db.query(RouteGroup).filter_by(id=group_id).first()
    return templates.TemplateResponse("group.html", {"request": request, "group": group})


@app.get("/activity/{activity_id}", response_class=HTMLResponse)
def activity_detail(activity_id: int, request: Request, db: Session = Depends(get_db)):
    activity = db.query(Activity).filter_by(id=activity_id).first()
    return templates.TemplateResponse("activity.html", {"request": request, "activity": activity})


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

    added = 0
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
        activity = _save_activity(
            db, source="strava", external_id=str(a["id"]),
            name=a.get("name", "Run"), points=points,
            distance_m=a.get("distance", 0.0), duration_s=a.get("moving_time"),
            start_time=start_time,
        )
        if activity:
            added += 1
    db.commit()
    if added:
        rebuild_groups(db)
    return RedirectResponse("/", status_code=303)
