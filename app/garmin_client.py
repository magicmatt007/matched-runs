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
import secrets
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
    """Resume a saved session (created via the 'Connect Garmin' web login,
    or garmin_login.py) if one exists; falls back to a fresh credential
    login using env vars otherwise - those are optional, only needed as a
    fallback if a saved session doesn't exist or expires."""
    if not has_saved_session() and not is_configured():
        raise GarminAuthError(
            "No saved Garmin session, and GARMIN_EMAIL/GARMIN_PASSWORD aren't "
            "set either. Use the 'Connect Garmin' button on the homepage to log in."
        )

    os.makedirs(TOKENSTORE, exist_ok=True)
    client = Garmin(GARMIN_EMAIL or "", GARMIN_PASSWORD or "")
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
            f"Garmin login failed: {e}. If your saved session expired, use the "
            "'Connect Garmin' button on the homepage to log in again. If this "
            "used to work and just stopped, Garmin may have changed their auth "
            "flow again - check https://github.com/cyberjunky/python-garminconnect/issues"
        )

    return client


# Holds Garmin client instances mid-login while waiting for an MFA code to
# come back through a second HTTP request. In-memory only - fine for a
# single-user, single-process app; an entry that's never completed just sits
# here harmlessly until the process restarts.
_pending_mfa_logins = {}


def start_web_login(email: str, password: str):
    """Step 1 of the web-based login flow (replaces needing docker exec for
    MFA-enabled accounts). Returns ("done", None) if login completed
    immediately, or ("needs_mfa", token) if Garmin wants a code - pass that
    token into complete_web_login() along with what the user enters."""
    if not email or not password:
        raise GarminAuthError("Email and password are required.")

    os.makedirs(TOKENSTORE, exist_ok=True)
    client = Garmin(email=email, password=password, return_on_mfa=True)
    try:
        status, client_state = client.login()
    except GarminConnectTooManyRequestsError as e:
        raise GarminAuthError(
            f"Garmin is rate-limiting login attempts from this IP right now: {e}. "
            "Wait a while before retrying."
        )
    except GarminConnectAuthenticationError:
        raise GarminAuthError("Garmin rejected that email/password.")
    except Exception as e:
        raise GarminAuthError(f"Garmin login failed: {e}")

    if status == "needs_mfa":
        token = secrets.token_urlsafe(16)
        _pending_mfa_logins[token] = (client, client_state)
        return "needs_mfa", token

    try:
        client.client.dump(TOKENSTORE)
    except Exception as e:
        raise GarminAuthError(f"Login succeeded but saving the session failed: {e}")
    return "done", None


def complete_web_login(token: str, mfa_code: str):
    """Step 2: finishes a login that needed an MFA code."""
    entry = _pending_mfa_logins.pop(token, None)
    if not entry:
        raise GarminAuthError("This login attempt expired or was already used - please start over.")
    client, client_state = entry
    try:
        client.resume_login(client_state, mfa_code)
    except Exception as e:
        raise GarminAuthError(f"MFA verification failed: {e}. Please start over.")
    try:
        client.client.dump(TOKENSTORE)
    except Exception as e:
        raise GarminAuthError(f"Login succeeded but saving the session failed: {e}")


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
