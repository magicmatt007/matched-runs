"""Minimal decoder for Google's encoded polyline format, used by Strava's
`map.summary_polyline` field. No external dependency needed."""


def decode_polyline(polyline_str: str):
    if not polyline_str:
        return []

    index, lat, lng = 0, 0, 0
    coordinates = []
    length = len(polyline_str)

    while index < length:
        for is_lat in (True, False):
            shift, result = 0, 0
            while True:
                b = ord(polyline_str[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lng += delta
        coordinates.append((lat / 1e5, lng / 1e5))

    return coordinates
