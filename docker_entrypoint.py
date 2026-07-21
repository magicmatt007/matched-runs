#!/usr/bin/env python3
"""
Single entrypoint used for BOTH deployment methods:

- Standalone docker compose: /data/options.json doesn't exist, so this is a
  no-op and the app just reads whatever env vars docker-compose set from
  .env, exactly as before.
- Home Assistant app: Supervisor writes the user's configured options to
  /data/options.json. This loads it and exports each key, upper-cased, as
  an env var - e.g. options.json's "garmin_email" becomes GARMIN_EMAIL,
  matching exactly what app/garmin_client.py etc. already read. No other
  code needed to change to support both.
"""
import json
import os

OPTIONS_PATH = "/data/options.json"

if os.path.exists(OPTIONS_PATH):
    with open(OPTIONS_PATH) as f:
        options = json.load(f)
    for key, value in options.items():
        if value is None or value == "":
            continue
        os.environ[key.upper()] = str(value)

os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"])
