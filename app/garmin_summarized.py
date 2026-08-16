"""
Garmin's full account export ("Export Your Data") includes
DI-Connect-Fitness/*_summarizedActivities.json - a paginated dump of every
activity Garmin Connect knows about for the account, each with its real
display name and real numeric activity ID. Neither is present in the raw
.fit files themselves: a .fit file has no free-text title field at all
(fit_parser.py falls back to the uploaded filename), and the number
embedded in that filename is NOT the real Garmin activity ID - confirmed
directly against Garmin's own API, which 404s on it, even though it looks
plausible (same digit count as a real activity ID).

Unlike Strava's activities.csv (which maps by exact filename - see
strava_csv.py), there's no filename or ID shared between a raw .fit file
and its summarized-activity record, so the join here is by start time
instead (millisecond-precision in the JSON, second-precision from the
.fit file - matched within a small tolerance), cross-checked by distance
when more than one activity started in the same few seconds. Verified
directly against a real export: a .fit file parsing to distance=13947.63m
at 2020-04-12 06:52:11 matched a summarized record with
distance=1394762.98828125 (their unit is centimeters) at the same
timestamp, real name "Zurich Running", and a real activityId that Garmin's
own API confirmed resolves to that exact activity.
"""
import calendar
import json
import re
from datetime import timezone

# Garmin's export gives distance/elevation in centimeters and duration in
# milliseconds - convert to this app's own units (meters, seconds) once
# here rather than at every call site.
_CM_TO_M = 100.0
_MS_TO_S = 1000.0

# A trailing "_ws" tag Garmin adds to some winter-sport activityType
# values (e.g. "cross_country_skiing_ws", "skate_skiing_ws") - meaningless
# on its own once title-cased, so dropped the same way a "V2" version
# suffix already gets stripped elsewhere (normalize_activity_type, applied
# to whatever this module returns once it reaches _save_activity).
_TRAILING_WS_TAG_RE = re.compile(r"\s+ws$", re.IGNORECASE)


def _format_activity_type(raw):
    """Garmin's activityType values (e.g. "inline_skating",
    "resort_skiing_snowboarding_ws") use the same snake_case convention
    a .fit file's own `sport` field does (see fit_parser.py) - and this
    app already title-cases that the same simple way, so no separate
    vocabulary mapping is needed here the way Strava's activities.csv
    needed one (see strava_csv.py's STRAVA_TYPE_MAP - Strava's own words,
    like "Ride" for cycling, genuinely don't match Garmin's)."""
    if not raw:
        return None
    formatted = raw.replace("_", " ").title()
    formatted = _TRAILING_WS_TAG_RE.sub("", formatted).strip()
    return formatted or None


def parse_summarized_activities(data: bytes) -> list:
    """Returns a list of {"activity_id": str, "name": str or None,
    "activity_type": str or None, "start_ts_ms": int, "distance_m": float
    or None} - one per activity record found. Garmin paginates this
    export (e.g. a "_0_" and "_1001_" file for 1000 activities each) -
    call this once per uploaded file and combine the results into one
    lookup covering the whole account history (see
    build_start_time_index).

    activity_type matters because a raw .fit/.tcx file doesn't always
    carry an accurate one either - confirmed directly: a real activity
    Garmin itself classifies as "inline_skating" here came from a raw TCX
    file whose own Sport attribute could only say "Other" (TCX v2's Sport
    enum is limited to Running/Biking/Other, nothing else - not a parsing
    bug, a hard limit of the file format), even though the exported
    filename happened to say "Skating" (leaked from the original Polar
    device's own naming, not read by this app's TCX parser)."""
    try:
        payload = json.loads(data.decode("utf-8", errors="ignore"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []

    records = []
    for chunk in payload:
        if not isinstance(chunk, dict):
            continue
        for entry in (chunk.get("summarizedActivitiesExport") or []):
            activity_id = entry.get("activityId")
            begin_ts = entry.get("beginTimestamp")
            if not activity_id or not begin_ts:
                continue
            distance_cm = entry.get("distance")
            records.append({
                "activity_id": str(activity_id),
                "name": (entry.get("name") or "").strip() or None,
                "activity_type": _format_activity_type(entry.get("activityType")),
                "start_ts_ms": int(begin_ts),
                "distance_m": (distance_cm / _CM_TO_M) if distance_cm is not None else None,
            })
    return records


def build_start_time_index(records: list) -> dict:
    """Indexes records by start time rounded down to the second, for cheap
    lookup - the actual matching (small tolerance window + distance
    tie-break) happens in match_by_start_time below."""
    index = {}
    for r in records:
        key = r["start_ts_ms"] // int(_MS_TO_S)
        index.setdefault(key, []).append(r)
    return index


def _to_epoch_s(dt):
    """This app treats every stored start_time as naive-UTC regardless of
    source (fit_parser strips any tzinfo fitparse attaches; the database
    column itself is a plain, timezone-less DateTime) - calendar.timegm
    interprets a naive datetime's fields as UTC directly, unlike
    datetime.timestamp() which would wrongly assume the *system's* local
    timezone. A tz-aware datetime (shouldn't normally reach here, but
    cheap to handle correctly) is converted to UTC first instead."""
    if dt.tzinfo is not None:
        return int(dt.astimezone(timezone.utc).timestamp())
    return calendar.timegm(dt.timetuple())


def match_by_start_time(index: dict, start_time, distance_m=None, tolerance_s: int = 60):
    """Finds the summarized-activity record matching a locally-parsed
    activity's start_time, within `tolerance_s` seconds. Returns None if
    nothing in the index is that close. 60s (not tighter) because the two
    timestamps aren't always identical to the second even for a genuine
    match - confirmed directly: one real activity's .fit-parsed start time
    and its summarized-activity record's beginTimestamp were 15s apart,
    while distance matched to the centimeter. When more than one candidate
    falls in the window (two activities starting under a minute apart -
    rare for one person), the one with the closest distance_m wins, if
    both sides have a distance to compare; otherwise the first
    (closest-in-time) one."""
    if start_time is None or not index:
        return None
    target = _to_epoch_s(start_time)
    candidates = []
    for offset in range(-tolerance_s, tolerance_s + 1):
        candidates.extend(index.get(target + offset, []))
    if not candidates:
        return None
    if len(candidates) > 1 and distance_m:
        candidates.sort(key=lambda r: abs((r["distance_m"] or 0.0) - distance_m))
    best = candidates[0]

    # A wide-ish time window (see docstring) makes a distance sanity check
    # worthwhile even for a single candidate - reject rather than risk
    # attaching the wrong name/link when a short unrelated recording
    # happens to start within a minute of this one and distance disagrees
    # by a lot. A generous margin (both a relative and an absolute floor,
    # so it doesn't get overly strict for very short activities) - real
    # matches seen in practice line up far more precisely than this.
    if distance_m and best["distance_m"]:
        margin = max(500.0, distance_m * 0.25)
        if abs(best["distance_m"] - distance_m) > margin:
            return None
    return best
