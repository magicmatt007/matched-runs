"""Parse GPX files into a simple dict of points/metadata."""
import gpxpy
from datetime import datetime


def parse_gpx_bytes(data: bytes, fallback_name: str = "Run"):
    gpx = gpxpy.parse(data.decode("utf-8", errors="ignore"))

    points = []
    start_time = None
    for track in gpx.tracks:
        for segment in track.segments:
            for p in segment.points:
                points.append((p.latitude, p.longitude))
                if p.time and start_time is None:
                    start_time = p.time

    # Some GPX exports (e.g. routes without tracks) use routes instead
    if not points:
        for route in gpx.routes:
            for p in route.points:
                points.append((p.latitude, p.longitude))

    if not points:
        raise ValueError("No track points found in GPX file")

    name = None
    if gpx.tracks and gpx.tracks[0].name:
        name = gpx.tracks[0].name
    elif gpx.name:
        name = gpx.name
    if not name:
        name = fallback_name

    activity_type = None
    if gpx.tracks and gpx.tracks[0].type:
        activity_type = gpx.tracks[0].type.replace("_", " ").title()

    try:
        distance_m = gpx.length_3d() or gpx.length_2d() or 0.0
    except Exception:
        distance_m = 0.0

    duration_s = None
    try:
        duration_s = gpx.get_duration()
    except Exception:
        pass

    if isinstance(start_time, datetime):
        start_time = start_time.replace(tzinfo=None)

    return {
        "name": name,
        "activity_type": activity_type,
        "points": points,
        "distance_m": float(distance_m),
        "duration_s": duration_s,
        "start_time": start_time,
    }
