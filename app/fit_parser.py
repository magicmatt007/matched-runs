"""Parse .fit files (Garmin's native device format) into the same dict shape
gpx_parser produces, so both can feed the same import pipeline.

Garmin's official bulk account export (Account Settings > Data Management >
Export Your Data) delivers activities as .fit files, so this lets you import
your whole history without manually exporting GPX one activity at a time.
"""
import io
from datetime import datetime
import fitparse
from app.elevation_utils import gain_loss_from_elevations


def parse_fit_bytes(data: bytes, fallback_name: str = "Run"):
    fitfile = fitparse.FitFile(io.BytesIO(data))

    points = []
    altitudes = []
    heart_rates = []  # per-point aligned with points, None where missing
    start_time = None
    for record in fitfile.get_messages("record"):
        lat = record.get_value("position_lat")
        lon = record.get_value("position_long")
        if lat is None or lon is None:
            continue
        # FIT stores coordinates as 32-bit semicircles
        lat_deg = lat * (180 / 2 ** 31)
        lon_deg = lon * (180 / 2 ** 31)
        points.append((lat_deg, lon_deg))
        # "enhanced_altitude" has higher precision than plain "altitude" when
        # present; fall back to the plain field otherwise.
        alt = record.get_value("enhanced_altitude")
        if alt is None:
            alt = record.get_value("altitude")
        altitudes.append(alt)
        heart_rates.append(record.get_value("heart_rate"))
        ts = record.get_value("timestamp")
        if ts and start_time is None:
            start_time = ts

    # No raise here for an empty points list - indoor activities (pool
    # swims, gym sessions, treadmill runs on some devices) genuinely have
    # no GPS track, but still have valid distance/duration/heart
    # rate/calories worth importing. They just can't be route-matched,
    # since there's no route to match against - see matcher.py, which
    # explicitly excludes routeless activities from all matching/dedup.

    distance_m = 0.0
    duration_s = None
    sport = None
    elevation_gain_m = None
    elevation_loss_m = None
    avg_heart_rate = None
    max_heart_rate = None
    avg_cadence = None
    calories = None

    for session in fitfile.get_messages("session"):
        d = session.get_value("total_distance")
        if d:
            distance_m = float(d)
        t = session.get_value("total_elapsed_time")
        if t:
            duration_s = float(t)
        sp = session.get_value("sport")
        if sp:
            sport = str(sp)
        st = session.get_value("start_time")
        if st and start_time is None:
            start_time = st

        # These are device-computed (barometric altimeter for
        # ascent/descent, chest strap or wrist sensor for heart rate) - more
        # reliable than anything we could derive ourselves, so prefer them
        # directly over the point-level fallback below.
        ascent = session.get_value("total_ascent")
        if ascent is not None:
            elevation_gain_m = float(ascent)
        descent = session.get_value("total_descent")
        if descent is not None:
            elevation_loss_m = float(descent)
        avg_hr = session.get_value("avg_heart_rate")
        if avg_hr is not None:
            avg_heart_rate = float(avg_hr)
        max_hr = session.get_value("max_heart_rate")
        if max_hr is not None:
            max_heart_rate = float(max_hr)
        cad = session.get_value("avg_cadence")
        if cad is not None:
            avg_cadence = float(cad)
        cal = session.get_value("total_calories")
        if cal is not None:
            calories = float(cal)

    # Fallback: if the session message didn't include a device-computed
    # ascent/descent (some FIT files omit it), derive it from the
    # point-by-point altitude readings instead.
    if elevation_gain_m is None or elevation_loss_m is None:
        computed_gain, computed_loss = gain_loss_from_elevations(altitudes)
        if elevation_gain_m is None:
            elevation_gain_m = computed_gain
        if elevation_loss_m is None:
            elevation_loss_m = computed_loss

    name = fallback_name
    activity_type = sport.replace("_", " ").title() if sport else None

    if isinstance(start_time, datetime):
        start_time = start_time.replace(tzinfo=None)

    hr_values = [h for h in heart_rates if h is not None]

    return {
        "name": name,
        "activity_type": activity_type,
        "points": points,
        "distance_m": distance_m,
        "duration_s": duration_s,
        "start_time": start_time,
        "elevation_gain_m": elevation_gain_m,
        "elevation_loss_m": elevation_loss_m,
        "avg_heart_rate": avg_heart_rate,
        "max_heart_rate": max_heart_rate,
        "avg_cadence": avg_cadence,
        "calories": calories,
        "elevation_profile": altitudes if any(a is not None for a in altitudes) else None,
        "heart_rate_profile": heart_rates if hr_values else None,
    }
