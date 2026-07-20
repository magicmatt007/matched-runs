#!/usr/bin/env python3
"""
One-time interactive Garmin Connect login.

Run this ONCE if your Garmin account has MFA/2FA enabled - a background sync
job has no way to answer an MFA code prompt on its own, so this needs a human
at a terminal the first time. If your account does NOT have MFA enabled, you
can skip this entirely; setting GARMIN_EMAIL / GARMIN_PASSWORD in .env is
enough and the app will log in automatically.

Usage (from the host, with the container's /data volume mounted):

    docker compose run --rm -it matched-runs python garmin_login.py

This saves a session under /data/garmin_tokens, the same volume the running
app uses, so the saved session is picked up automatically afterwards -
tokens are valid for roughly a year before you'd need to run this again.
"""
import os
import getpass

from garminconnect import Garmin

TOKENSTORE = "/data/garmin_tokens"


def main():
    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ")
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")

    client = Garmin(email, password)
    print("Logging in... (you'll be prompted here if your account needs an MFA code)")
    os.makedirs(TOKENSTORE, exist_ok=True)
    client.login(TOKENSTORE)  # both authenticates and persists tokens to TOKENSTORE

    print(f"Success! Session saved to {TOKENSTORE} - the app will reuse this automatically.")


if __name__ == "__main__":
    main()
