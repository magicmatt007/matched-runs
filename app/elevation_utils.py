"""
Elevation gain/loss from a raw sequence of altitude readings.

Used as a fallback for formats that don't provide a device-computed total
(FIT's session message usually does, via a barometric altimeter - GPX and
TCX generally don't, so this derives it from the point-by-point elevation
values instead). Naive consecutive-point summation like this is noisier
than a proper device-computed total (raw GPS/barometric altitude jitter
inflates both gain and loss somewhat), but it's a reasonable approximation
without pulling in a smoothing dependency.
"""
import math


def gain_loss_from_elevations(elevations):
    """elevations: list of floats (meters), possibly containing None for
    points where elevation wasn't available. Returns (gain_m, loss_m), or
    (None, None) if there aren't at least 2 valid readings to compare."""
    valid = [e for e in elevations if e is not None]
    if len(valid) < 2:
        return None, None

    gain = 0.0
    loss = 0.0
    prev = None
    for e in elevations:
        if e is None:
            continue
        if prev is not None:
            delta = e - prev
            if delta > 0:
                gain += delta
            else:
                loss += -delta
        prev = e

    return gain, loss


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(min(1, math.sqrt(a)))


def track_length(points):
    """Total GPS-derived distance (meters) along a list of (lat, lon)
    points - used as a fallback when a format's own reported distance is
    missing or unreliable (confirmed in practice: some TCX exports carry
    only a single, obviously-wrong Lap-level DistanceMeters like 9.9m for
    an 82km ride, with no per-trackpoint distance at all - GPS positions
    were present and correct throughout, so summing consecutive-point
    distances gives the real total instead of trusting that broken
    summary field)."""
    total = 0.0
    for i in range(1, len(points)):
        total += haversine(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
    return total
