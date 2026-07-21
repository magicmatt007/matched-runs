"""
Activity type cleanup.

Two independent problems, both fixed here:

1. Garmin's internal activity-type keys sometimes carry a version suffix
   (e.g. "kayaking_v2") when Garmin revises the sensor/algorithm profile
   behind a sport. Our parsers title-case the raw key, which turned that
   into a visible "Kayaking V2" label. Stripped here, for every source -
   it's not a real distinct type, just an internal versioning artifact.

2. Older watches (e.g. a Polar synced into Strava years ago) had a more
   limited choice of activity types, so the same real activity might have
   been logged as "Walking" one year and "Hiking" another. For anything
   NOT sourced from a live Garmin sync (which reports today's real type),
   these legacy choices get merged into one canonical type.
"""
import re

# real-type -> canonical-type, for sources OTHER than "garmin" only
LEGACY_TYPE_MERGE = {
    "hiking": "Hiking",
    "walking": "Hiking",
    "kayaking": "Kayaking",
    "rowing": "Kayaking",
}

_VERSION_SUFFIX_RE = re.compile(r"\s*v\d+$", re.IGNORECASE)


def strip_version_suffix(type_str: str) -> str:
    if not type_str:
        return type_str
    return _VERSION_SUFFIX_RE.sub("", type_str).strip()


def normalize_activity_type(raw_type: str, source: str) -> str:
    if not raw_type:
        return "Other"

    cleaned = strip_version_suffix(raw_type)

    if source != "garmin":
        key = cleaned.strip().lower()
        if key in LEGACY_TYPE_MERGE:
            cleaned = LEGACY_TYPE_MERGE[key]

    return cleaned or "Other"
