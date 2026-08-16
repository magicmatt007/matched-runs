"""
Activity type cleanup.

Garmin's internal activity-type keys sometimes carry a version suffix
(e.g. "kayaking_v2") when Garmin revises the sensor/algorithm profile
behind a sport. Our parsers title-case the raw key, which turned that
into a visible "Kayaking V2" label. `normalize_activity_type` strips
this automatically on every import - it's a pure cosmetic artifact, not
a real distinct type, regardless of source or date.

(Merging one activity type into another - e.g. because an old watch only
offered a limited choice of types, so the same real activity got logged
as "Walking" one year and "Hiking" another - used to be a second,
hardcoded cleanup step here too. That's now the fully user-driven "Merge
activity types" form on the Import & Sync page instead - see the
/merge-activity-types route in main.py - since a fixed pair of hardcoded
mappings couldn't cover whatever mix of types someone's own watch history
actually has.)
"""
import re

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

