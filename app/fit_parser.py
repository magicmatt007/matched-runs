"""Parse .fit files (Garmin's native device format) into the same dict shape
gpx_parser produces, so both can feed the same import pipeline.

Garmin's official bulk account export (Account Settings > Data Management >
Export Your Data) delivers activities as .fit files, so this lets you import
your whole history without manually exporting GPX one activity at a time.
"""
import io
from datetime import datetime
import fitparse


def parse_fit_bytes(data: bytes, fallback_name: str = "Run"):
    fitfile = fitparse.FitFile(io.BytesIO(data))

    points = []
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
        ts = record.get_value("timestamp")
        if ts and start_time is None:
            start_time = ts

    if not points:
        raise ValueError("No GPS points found in FIT file (indoor activity, or a device without GPS?)")

    distance_m = 0.0
    duration_s = None
    sport = None
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

    name = fallback_name
    activity_type = sport.replace("_", " ").title() if sport else None

    if isinstance(start_time, datetime):
        start_time = start_time.replace(tzinfo=None)

    return {
        "name": name,
        "activity_type": activity_type,
        "points": points,
        "distance_m": distance_m,
        "duration_s": duration_s,
        "start_time": start_time,
    }
