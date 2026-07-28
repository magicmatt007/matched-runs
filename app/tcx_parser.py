"""Parse .tcx files (Training Center XML) - the format Strava commonly used
for activities synced in from third-party devices/apps (e.g. an old Polar
account), rather than recorded directly on a Garmin device.

Written with plain xml.etree + namespace-agnostic tag matching, since TCX
files in the wild come with slightly different namespace declarations
depending on which vendor produced them.
"""
import xml.etree.ElementTree as ET
from datetime import datetime
from app.elevation_utils import gain_loss_from_elevations


def _local(tag):
    """Strip the XML namespace off a tag, e.g. '{...}Trackpoint' -> 'Trackpoint'."""
    return tag.split("}")[-1] if "}" in tag else tag


def parse_tcx_bytes(data: bytes, fallback_name: str = "Run"):
    # Some Strava exports have stray leading whitespace before the XML
    # declaration - see gpx_parser.py for the same fix and why.
    first_tag = data.find(b"<")
    if first_tag > 0:
        data = data[first_tag:]

    root = ET.fromstring(data)

    sport = None
    for el in root.iter():
        if _local(el.tag) == "Activity":
            sport = el.attrib.get("Sport")
            break

    points = []
    altitudes = []
    heart_rates = []  # flat list of every HR reading found, for avg/max summary stats
    heart_rate_series = []  # per-point aligned with points/altitudes, for the chart
    times = []  # per-point aligned, parsed datetime or None
    cadences = []
    start_time = None
    last_distance = None

    for tp in root.iter():
        if _local(tp.tag) != "Trackpoint":
            continue
        lat = lon = time_val = dist_val = alt_val = point_hr = None
        for child in tp:
            name = _local(child.tag)
            if name == "Position":
                for pc in child:
                    pname = _local(pc.tag)
                    if pname == "LatitudeDegrees" and pc.text:
                        lat = float(pc.text)
                    elif pname == "LongitudeDegrees" and pc.text:
                        lon = float(pc.text)
            elif name == "Time" and child.text:
                time_val = child.text
            elif name == "DistanceMeters" and child.text:
                try:
                    dist_val = float(child.text)
                except ValueError:
                    pass
            elif name == "AltitudeMeters" and child.text:
                try:
                    alt_val = float(child.text)
                except ValueError:
                    pass
            elif name == "HeartRateBpm":
                for hc in child:
                    if _local(hc.tag) == "Value" and hc.text:
                        try:
                            point_hr = float(hc.text)
                            heart_rates.append(point_hr)
                        except ValueError:
                            pass

        # Cadence can appear as a plain <Cadence> element, or nested inside
        # vendor extensions (e.g. Garmin's RunCadence) - search the whole
        # trackpoint subtree rather than assuming one fixed location.
        for el in tp.iter():
            el_tag = _local(el.tag)
            if el_tag in ("Cadence", "RunCadence") and el.text:
                try:
                    cadences.append(float(el.text))
                except ValueError:
                    pass

        point_time = None
        if time_val:
            try:
                point_time = datetime.fromisoformat(time_val.replace("Z", "+00:00"))
            except ValueError:
                pass

        # IMPORTANT: capture time/distance regardless of whether GPS
        # position is present. Indoor activities (pool swims especially)
        # often have Time/DistanceMeters per trackpoint but NO Position at
        # all - gating this behind a valid lat/lon (like the points.append
        # below correctly does) would silently lose distance/start_time
        # for every indoor activity.
        if point_time and start_time is None:
            start_time = point_time
        if dist_val is not None:
            last_distance = dist_val

        if lat is None or lon is None:
            continue
        points.append((lat, lon))
        altitudes.append(alt_val)
        heart_rate_series.append(point_hr)
        times.append(point_time)

    duration_s = None
    total_lap_time = 0.0
    found_lap_time = False
    total_lap_distance = 0.0
    found_lap_distance = False
    total_calories = 0.0
    found_calories = False
    lap_avg_hrs = []
    lap_max_hrs = []

    for lap in root.iter():
        if _local(lap.tag) != "Lap":
            continue
        # Some TCX files only carry a start time as an attribute on <Lap>
        # itself (no per-trackpoint <Time>, or no trackpoints at all for a
        # summary-only lap) - use it as a fallback.
        lap_start_attr = lap.attrib.get("StartTime")
        if lap_start_attr and start_time is None:
            try:
                start_time = datetime.fromisoformat(lap_start_attr.replace("Z", "+00:00"))
            except ValueError:
                pass

        for child in lap:
            child_tag = _local(child.tag)
            if child_tag == "TotalTimeSeconds" and child.text:
                try:
                    total_lap_time += float(child.text)
                    found_lap_time = True
                except ValueError:
                    pass
            elif child_tag == "DistanceMeters" and child.text:
                try:
                    total_lap_distance += float(child.text)
                    found_lap_distance = True
                except ValueError:
                    pass
            elif child_tag == "Calories" and child.text:
                try:
                    total_calories += float(child.text)
                    found_calories = True
                except ValueError:
                    pass
            elif child_tag == "AverageHeartRateBpm":
                for hc in child:
                    if _local(hc.tag) == "Value" and hc.text:
                        try:
                            lap_avg_hrs.append(float(hc.text))
                        except ValueError:
                            pass
            elif child_tag == "MaximumHeartRateBpm":
                for hc in child:
                    if _local(hc.tag) == "Value" and hc.text:
                        try:
                            lap_max_hrs.append(float(hc.text))
                        except ValueError:
                            pass

    if found_lap_time:
        duration_s = total_lap_time

    # Prefer the cumulative distance seen on trackpoints (most granular);
    # fall back to summed Lap-level totals if no trackpoint ever carried a
    # DistanceMeters value at all.
    if last_distance is None and found_lap_distance:
        last_distance = total_lap_distance

    # Elapsed seconds since the activity's start, one per point - computed
    # here (after start_time is fully finalized, since the Lap-level
    # fallback above can also supply it) rather than during the trackpoint
    # loop. Needed to derive pace at each point later - not itself
    # displayed anywhere.
    time_profile = None
    if start_time is not None and any(t is not None for t in times):
        time_profile = [(t - start_time).total_seconds() if t is not None else None for t in times]

    if isinstance(start_time, datetime):
        start_time = start_time.replace(tzinfo=None)

    elevation_gain_m, elevation_loss_m = gain_loss_from_elevations(altitudes)

    # Prefer trackpoint-level heart rate (naturally aggregates correctly
    # across multiple laps without needing a weighted average); fall back
    # to lap-level summaries if no trackpoint HR data was present at all.
    if heart_rates:
        avg_heart_rate = sum(heart_rates) / len(heart_rates)
        max_heart_rate = max(heart_rates)
    elif lap_avg_hrs or lap_max_hrs:
        avg_heart_rate = (sum(lap_avg_hrs) / len(lap_avg_hrs)) if lap_avg_hrs else None
        max_heart_rate = max(lap_max_hrs) if lap_max_hrs else None
    else:
        avg_heart_rate = None
        max_heart_rate = None

    # No raise here for an empty points list - indoor activities (e.g.
    # pool swims recorded without Position elements) are still worth
    # importing without a route. See matcher.py, which explicitly excludes
    # routeless activities from all matching/dedup.

    return {
        "name": fallback_name,
        "activity_type": sport,
        "points": points,
        "distance_m": last_distance or 0.0,
        "duration_s": duration_s,
        "start_time": start_time,
        "elevation_gain_m": elevation_gain_m,
        "elevation_loss_m": elevation_loss_m,
        "avg_heart_rate": avg_heart_rate,
        "max_heart_rate": max_heart_rate,
        "avg_cadence": (sum(cadences) / len(cadences)) if cadences else None,
        "calories": total_calories if found_calories else None,
        "elevation_profile": altitudes if any(a is not None for a in altitudes) else None,
        "heart_rate_profile": heart_rate_series if any(h is not None for h in heart_rate_series) else None,
        "time_profile": time_profile,
    }
