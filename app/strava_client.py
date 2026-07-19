import os
import time
from urllib.parse import urlencode
import httpx

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_REDIRECT_URI = os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:8000/strava/callback")

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"


def is_configured():
    return bool(STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET)


def get_authorize_url():
    params = {
        "client_id": STRAVA_CLIENT_ID,
        "redirect_uri": STRAVA_REDIRECT_URI,
        "response_type": "code",
        # "force" (not "auto") makes Strava always show the consent screen and
        # re-grant scope explicitly. With "auto", if this app was ever
        # authorized before (e.g. by you as the app's own creator) with a
        # narrower scope like just "read", Strava silently reuses that old
        # grant and activity:read_all never gets attached to the token —
        # which is the #1 cause of a 403 on /athlete/activities.
        "approval_prompt": "force",
        "scope": "read,activity:read_all",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str):
    resp = httpx.post(TOKEN_URL, data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str):
    resp = httpx.post(TOKEN_URL, data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })
    resp.raise_for_status()
    return resp.json()


def ensure_valid_token(token_row):
    """Given a StravaToken db row, refresh it if expired. Returns access_token."""
    if token_row.expires_at - 60 > time.time():
        return token_row.access_token
    try:
        data = refresh_access_token(token_row.refresh_token)
    except httpx.HTTPStatusError as e:
        raise StravaAuthError(
            f"Could not refresh the Strava token (it may have been revoked): {e}"
        )
    token_row.access_token = data["access_token"]
    token_row.refresh_token = data["refresh_token"]
    token_row.expires_at = data["expires_at"]
    return token_row.access_token


class StravaAuthError(Exception):
    """Raised when Strava rejects our token (expired/revoked/missing scope)."""
    pass


def fetch_activities(access_token: str, after_epoch: int = None, per_page: int = 100, max_pages: int = 10):
    """Fetch running activities from Strava, newest first, paginated."""
    headers = {"Authorization": f"Bearer {access_token}"}
    activities = []
    for page in range(1, max_pages + 1):
        params = {"per_page": per_page, "page": page}
        if after_epoch:
            params["after"] = after_epoch
        resp = httpx.get(f"{API_BASE}/athlete/activities", headers=headers, params=params, timeout=30)
        if resp.status_code in (401, 403):
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            if resp.status_code == 401:
                reason = "Strava says this access token is no longer valid (expired or revoked)."
            else:
                reason = "Strava says this token is missing required permissions (activity:read_all)."
            raise StravaAuthError(f"{reason} Detail: {detail}")
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < per_page:
            break
    # Keep only runs (adjust here if you also want walks/hikes matched)
    return [a for a in activities if a.get("type") in ("Run", "TrailRun")]
