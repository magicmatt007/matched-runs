#!/usr/bin/env python3
"""
Single entrypoint used for BOTH deployment methods:

- Standalone docker compose: /data/options.json doesn't exist, so that step
  is a no-op and the app just reads whatever env vars docker-compose set
  from .env, exactly as before.
- Home Assistant app: Supervisor writes the user's configured options to
  /data/options.json. This loads it and exports each key, upper-cased, as
  an env var - e.g. options.json's "garmin_email" becomes GARMIN_EMAIL,
  matching exactly what app/garmin_client.py etc. already read. No other
  code needed to change to support both.

Also launches uvicorn programmatically (instead of via the CLI) so we can
attach a logging config that adds timestamps to uvicorn's own request logs,
matching the timestamped format the app's own logger uses.
"""
import json
import os

import uvicorn

OPTIONS_PATH = "/data/options.json"

if os.path.exists(OPTIONS_PATH):
    with open(OPTIONS_PATH) as f:
        options = json.load(f)
    for key, value in options.items():
        if value is None or value == "":
            continue
        os.environ[key.upper()] = str(value)

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(asctime)s %(levelprefix)s %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
        "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"},
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_config=LOGGING_CONFIG)
