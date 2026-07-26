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
