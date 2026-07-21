"""
Strava's full account export includes a top-level `activities.csv` that maps
each exported activity file to the title you actually gave it on Strava
(plus type, description, gear, etc.) - the raw GPX/FIT/TCX files themselves
generally don't carry that title. This recovers it.
"""
import csv
import io


def parse_strava_activities_csv(data: bytes) -> dict:
    """Returns {basename(filename).lower(): "Activity Name", ...}"""
    text = data.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))

    name_map = {}
    for row in reader:
        filename = (row.get("Filename") or "").strip()
        title = (row.get("Activity Name") or "").strip()
        if not filename or not title:
            continue
        base = filename.rsplit("/", 1)[-1].strip().lower()
        if base:
            name_map[base] = title

    return name_map
