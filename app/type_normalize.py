"""
Activity type cleanup.

Two independent problems, handled separately:

1. Garmin's internal activity-type keys sometimes carry a version suffix
   (e.g. "kayaking_v2") when Garmin revises the sensor/algorithm profile
   behind a sport. Our parsers title-case the raw key, which turned that
   into a visible "Kayaking V2" label. `normalize_activity_type` strips
   this automatically on every import - it's a pure cosmetic artifact, not
   a real distinct type, regardless of source or date.

2. Older watches had a more limited choice of activity types, so the same
   real activity might have been logged as "Walking" one year and "Hiking"
   another, purely because of which watch was in use at the time - NOT
   because of which service it was imported through. Since we can't know
   "which watch was in use" directly, this is exposed as a manually
   triggered, date-scoped cleanup instead (`merge_legacy_type`, used by the
   /merge-legacy-types route in main.py): activities before a
   user-specified cutoff date get old-watch types merged into one
   canonical type; activities after it are left alone.
"""
import re

# old-watch-limited-choice type -> canonical type
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


def normalize_activity_type(raw_type: str) -> str:
    """Cosmetic cleanup only: strips Garmin's internal version suffixes.
    Applied automatically on every import."""
    if not raw_type:
        return "Other"
    return strip_version_suffix(raw_type) or "Other"


def merge_legacy_type(type_str: str) -> str:
    """Maps an old watch-limited type choice to its canonical type. NOT
    applied automatically - see module docstring. Returns type_str
    unchanged if there's no mapping for it."""
    if not type_str:
        return type_str
    key = type_str.strip().lower()
    return LEGACY_TYPE_MERGE.get(key, type_str)

