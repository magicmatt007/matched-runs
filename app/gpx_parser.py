"""Parse GPX files into a simple dict of points/metadata."""
import gpxpy
from datetime import datetime
from app.elevation_utils import gain_loss_from_elevations


def _local_tag(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def parse_gpx_bytes(data: bytes, fallback_name: str = "Run"):
    # Some Strava exports have stray leading whitespace before the XML
    # declaration, which strict XML parsers reject outright since <?xml
    # ...?> must be the very first thing in the document. Confirmed on TCX
    # exports from the same batch tooling - stripping this proactively
    # here too, since it's a no-op for any already-well-formed file.
    first_tag = data.find(b"<")
    if first_tag > 0:
        data = data[first_tag:]

    gpx = gpxpy.parse(data.decode("utf-8", errors="ignore"))

    points = []
    elevations = []
    heart_rates = []
    cadences = []
    start_time = None
    for track in gpx.tracks:
        for segment in track.segments:
            for p in segment.points:
                points.append((p.latitude, p.longitude))
                elevations.append(p.elevation)
                if p.time and start_time is None:
                    start_time = p.time
                # Best-effort: Garmin's TrackPointExtension (gpxtpx:hr /
                # gpxtpx:cad) isn't a standard GPX field - only present if
                # the exporting device/app included it, so this won't find
                # anything on every GPX file.
                for ext in (p.extensions or []):
                    for child in ext.iter():
                        tag = _local_tag(child.tag)
                        if tag == "hr" and child.text:
                            try:
                                heart_rates.append(float(child.text))
                            except ValueError:
                                pass
                        elif tag == "cad" and child.text:
                            try:
                                cadences.append(float(child.text))
                            except ValueError:
                                pass

    # Some GPX exports (e.g. routes without tracks) use routes instead
    if not points:
        for route in gpx.routes:
            for p in route.points:
                points.append((p.latitude, p.longitude))
                elevations.append(p.elevation)

    # No raise here for an empty points list - see fit_parser.py for why
    # (indoor activities are still worth importing without a route).

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

    elevation_gain_m, elevation_loss_m = gain_loss_from_elevations(elevations)

    return {
        "name": name,
        "activity_type": activity_type,
        "points": points,
        "distance_m": float(distance_m),
        "duration_s": duration_s,
        "start_time": start_time,
        "elevation_gain_m": elevation_gain_m,
        "elevation_loss_m": elevation_loss_m,
        "avg_heart_rate": (sum(heart_rates) / len(heart_rates)) if heart_rates else None,
        "max_heart_rate": max(heart_rates) if heart_rates else None,
        "avg_cadence": (sum(cadences) / len(cadences)) if cadences else None,
        "calories": None,  # not a standard/common GPX extension field
    }
