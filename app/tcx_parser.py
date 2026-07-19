"""Parse .tcx files (Training Center XML) - the format Strava commonly used
for activities synced in from third-party devices/apps (e.g. an old Polar
account), rather than recorded directly on a Garmin device.

Written with plain xml.etree + namespace-agnostic tag matching, since TCX
files in the wild come with slightly different namespace declarations
depending on which vendor produced them.
"""
import xml.etree.ElementTree as ET
from datetime import datetime


def _local(tag):
    """Strip the XML namespace off a tag, e.g. '{...}Trackpoint' -> 'Trackpoint'."""
    return tag.split("}")[-1] if "}" in tag else tag


def parse_tcx_bytes(data: bytes, fallback_name: str = "Run"):
    root = ET.fromstring(data)

    sport = None
    for el in root.iter():
        if _local(el.tag) == "Activity":
            sport = el.attrib.get("Sport")
            break

    points = []
    start_time = None
    last_distance = None

    for tp in root.iter():
        if _local(tp.tag) != "Trackpoint":
            continue
        lat = lon = time_val = dist_val = None
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

        if lat is None or lon is None:
            continue
        points.append((lat, lon))
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
    for lap in root.iter():
        if _local(lap.tag) == "Lap":
            for child in lap:
                if _local(child.tag) == "TotalTimeSeconds" and child.text:
                    try:
                        total_lap_time += float(child.text)
                        found_lap_time = True
                    except ValueError:
                        pass
    if found_lap_time:
        duration_s = total_lap_time

    if isinstance(start_time, datetime):
        start_time = start_time.replace(tzinfo=None)

    return {
        "name": sport or fallback_name,
        "points": points,
        "distance_m": last_distance or 0.0,
        "duration_s": duration_s,
        "start_time": start_time,
    }
