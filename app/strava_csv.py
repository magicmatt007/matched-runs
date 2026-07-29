"""
Strava's full account export includes a top-level `activities.csv` that maps
each exported activity file to the title you actually gave it on Strava
(plus type, description, gear, etc.) - the raw GPX/FIT/TCX files themselves
generally don't carry that title. This recovers it.

Also recovers the real Strava activity ID from the "Activity ID" column, so
a link back to the original activity on strava.com can be constructed for
file-based imports too, not just live-synced ones. Deliberately NOT derived
from the exported filename itself - an earlier attempt at that produced
links to entirely different people's activities (confirmed via direct
testing), even though the filename numbers looked plausible.
"""
import csv
import io


def parse_strava_activities_csv(data: bytes) -> dict:
    """Returns {basename(filename).lower(): {"title": "...", "activity_id": "..."}, ...}
    (either key may be absent if that column was blank for that row)."""
    text = data.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))

    overrides = {}
    for row in reader:
        filename = (row.get("Filename") or "").strip()
        title = (row.get("Activity Name") or "").strip()
        activity_id = (row.get("Activity ID") or "").strip()
        if not filename:
            continue
        base = filename.rsplit("/", 1)[-1].strip().lower()
        if not base:
            continue
        entry = {}
        if title:
            entry["title"] = title
        if activity_id:
            entry["activity_id"] = activity_id
        if entry:
            overrides[base] = entry

    return overrides
