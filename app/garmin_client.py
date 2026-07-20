"""
Unofficial Garmin Connect client, used to automatically pull new activities.

There is no public Garmin API for individual/hobbyist use (Garmin Connect's
developer program is an enterprise/partner program), so this uses
python-garminconnect (https://github.com/cyberjunky/python-garminconnect),
the same approach community tools like GarminDB use: it logs into your
Garmin account directly and talks to the same endpoints the mobile app uses.

This is unofficial and can break whenever Garmin changes their login flow
(it has happened before - the underlying `garth` auth library was recently
deprecated after exactly that). If sync suddenly stops working, check
https://github.com/cyberjunky/python-garminconnect/issues for a fix/update
before assuming this code is wrong.
"""
import os
import logging
from datetime import datetime

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

logger = logging.getLogger("matched_runs.garmin")

GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")
TOKENSTORE = "/data/garmin_tokens"


class GarminAuthError(Exception):
    pass


def is_configured():
    return bool(GARMIN_EMAIL and GARMIN_PASSWORD)


def has_saved_session():
    return os.path.isdir(TOKENSTORE) and bool(os.listdir(TOKENSTORE))


def get_client():
    """Resume a saved session (created via garmin_login.py) if one exists;
    falls back to a fresh credential login otherwise. Accounts with MFA/2FA
    enabled MUST run garmin_login.py interactively first - a background job
    has no way to answer an MFA prompt."""
    if not is_configured():
        raise GarminAuthError("GARMIN_EMAIL / GARMIN_PASSWORD are not set in .env")

    os.makedirs(TOKENSTORE, exist_ok=True)
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    try:
        # login(tokenstore) both loads cached tokens from this path if
        # present/valid, and saves freshly obtained ones back to it after a
        # credential login - no separate "save" step needed.
        client.login(TOKENSTORE)
    except GarminConnectTooManyRequestsError as e:
        raise GarminAuthError(
            f"Garmin is rate-limiting login attempts from this IP right now: {e}. "
            "Wait a while before retrying, and consider increasing "
            "GARMIN_SYNC_INTERVAL_MINUTES in .env so this happens less often."
        )
    except Exception as e:
        raise GarminAuthError(
            f"Garmin login failed: {e}. If your account has MFA/2FA enabled, "
            "run the one-time interactive login first: "
            "`docker compose run --rm -it matched-runs python garmin_login.py`. "
            "If this used to work and just stopped, Garmin may have changed "
            "their auth flow again - check "
            "https://github.com/cyberjunky/python-garminconnect/issues"
        )

    return client


def fetch_new_activities(client, after: datetime = None, limit: int = 200):
    """Return Garmin activity summary dicts newer than `after` (or all of
    the most recent `limit` if after is None)."""
    activities = client.get_activities(0, limit)
    if after is None:
        return activities

    results = []
    for a in activities:
        start_str = a.get("startTimeLocal") or a.get("startTimeGMT")
        if not start_str:
            continue
        try:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if start_dt > after:
            results.append(a)
    return results


def download_gpx(client, activity_id):
    fmt = getattr(Garmin, "ActivityDownloadFormat", None)
    dl_fmt = fmt.GPX if fmt else "gpx"
    return client.download_activity(activity_id, dl_fmt=dl_fmt)
