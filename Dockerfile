FROM python:3.11-slim

# These build args are supplied automatically by Home Assistant's Supervisor
# when building this as an app; they're simply empty/unused for a plain
# `docker compose build`, which is harmless.
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendor Leaflet locally at build time (your machine has normal internet
# access here, unlike some locked-down browsers/networks) so the map in the
# browser doesn't depend on unpkg.com being reachable at runtime.
RUN mkdir -p /code/app/static/vendor/leaflet/images && \
    curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/leaflet.js -o /code/app/static/vendor/leaflet/leaflet.js && \
    curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/leaflet.css -o /code/app/static/vendor/leaflet/leaflet.css && \
    curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png -o /code/app/static/vendor/leaflet/images/marker-icon.png && \
    curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png -o /code/app/static/vendor/leaflet/images/marker-icon-2x.png && \
    curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png -o /code/app/static/vendor/leaflet/images/marker-shadow.png

COPY app ./app
COPY garmin_login.py .
COPY docker_entrypoint.py .

RUN mkdir -p /data/gpx

EXPOSE 8000

CMD ["python", "docker_entrypoint.py"]