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

Also recovers the real activity type from the "Activity Type" column, for
the same reason as the title: a raw TCX file's own `Sport` attribute can
only ever be "Running", "Biking", or "Other" - that's the entire enum the
TCX v2 schema allows, so anything else (confirmed directly: a real activity
Strava itself classifies as "Inline Skate") gets recorded as "Other" right
at the source, nothing this app's own parsing could recover on its own.
"""
import csv
import io

# Strava's own CSV vocabulary ("Run", "Ride", "Alpine Ski", ...) doesn't
# match what this app already uses elsewhere (Garmin's own naming, e.g.
# "Running", "Cycling", "Alpine Skiing") - passing Strava's raw string
# through unchanged would silently split what should be one activity type
# into two different-looking ones (breaking cycling-vs-not detection
# in particular, e.g. "Ride" doesn't contain "cycl"/"bik" the way
# "Cycling"/"Biking" do - see main.py's _is_cycling_type), and fragment
# the type filter dropdown / "Merge activity types" checkbox list. Only
# a recognized mapping is ever applied - see parse_strava_activities_csv.
STRAVA_TYPE_MAP = {
    "Run": "Running",
    "Walk": "Walking",
    "Ride": "Cycling",
    "Hike": "Hiking",
    "Swim": "Swimming",
    "Kayaking": "Kayaking",
    "Rowing": "Rowing",
    "Alpine Ski": "Alpine Skiing",
    "Nordic Ski": "Cross Country Skiing",
    "Backcountry Ski": "Backcountry Skiing",
    "Inline Skate": "Inline Skating",
    "Workout": "Training",
    "Weight Training": "Weight Training",
}


def parse_strava_activities_csv(data: bytes) -> dict:
    """Returns {basename(filename).lower(): {"title": "...", "activity_id":
    "...", "activity_type": "..."}, ...} (any key may be absent if that
    column was blank for that row, or - for activity_type specifically -
    wasn't a recognized Strava type, in which case whatever the raw file
    itself says is left alone rather than risk introducing an unmapped,
    inconsistent type string)."""
    text = data.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))

    overrides = {}
    for row in reader:
        filename = (row.get("Filename") or "").strip()
        title = (row.get("Activity Name") or "").strip()
        activity_id = (row.get("Activity ID") or "").strip()
        raw_type = (row.get("Activity Type") or "").strip()
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
        mapped_type = STRAVA_TYPE_MAP.get(raw_type)
        if mapped_type:
            entry["activity_type"] = mapped_type
        if entry:
            overrides[base] = entry

    return overrides
