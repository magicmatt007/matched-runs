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
    # Some Strava TCX exports have stray leading whitespace before the XML
    # declaration - the XML spec requires <?xml ...?> to be the very first
    # thing in the document with nothing preceding it at all, so even a
    # few leading spaces make strict parsers reject the file outright.
    # Strip anything before the first '<' rather than fail on this.
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
    heart_rates = []
    cadences = []
    start_time = None
    last_distance = None

    for tp in root.iter():
        if _local(tp.tag) != "Trackpoint":
            continue
        lat = lon = time_val = dist_val = alt_val = None
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
                            heart_rates.append(float(hc.text))
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

        if lat is None or lon is None:
            continue
        points.append((lat, lon))
        altitudes.append(alt_val)
        if time_val and start_time is None:
            try:
                start_time = datetime.fromisoformat(time_val.replace("Z", "+00:00"))
            except ValueError:
                pass
        if dist_val is not None:
            last_distance = dist_val

    if not points:
        raise ValueError("No GPS trackpoints found in TCX file")

    duration_s = None
    total_lap_time = 0.0
    found_lap_time = False
    total_calories = 0.0
    found_calories = False
    lap_avg_hrs = []
    lap_max_hrs = []

    for lap in root.iter():
        if _local(lap.tag) != "Lap":
            continue
        for child in lap:
            child_tag = _local(child.tag)
            if child_tag == "TotalTimeSeconds" and child.text:
                try:
                    total_lap_time += float(child.text)
                    found_lap_time = True
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
    }
