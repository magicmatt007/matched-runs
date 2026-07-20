"""
Route matching.

Approach (similar in spirit to what Strava does for "matched runs"):
1. Every track is resampled to a fixed number of points, evenly spaced by
   distance along the route. This makes two GPS traces of the same route
   directly comparable point-by-point, regardless of pace or GPS sampling
   rate differences.
2. Two tracks are considered a match if:
   - their total distances are within a tolerance of each other, AND
   - the mean point-to-point deviation (after resampling) is below a
     threshold, checked in both directions (to catch out-and-back routes
     run in reverse).
3. Matches are transitive-grouped via union-find, so all activities on the
   same route end up in one RouteGroup, exactly like Strava's grouping.
"""
import math
import os
import json
from app.models import Activity, RouteGroup

RESAMPLE_POINTS = 40

MATCH_DISTANCE_THRESHOLD_M = float(os.environ.get("MATCH_DISTANCE_THRESHOLD_M", 50))
MATCH_LENGTH_TOLERANCE = float(os.environ.get("MATCH_LENGTH_TOLERANCE", 0.15))


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(min(1, math.sqrt(a)))


def track_length(points):
    total = 0.0
    for i in range(1, len(points)):
        total += haversine(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
    return total


def resample_track(points, n=RESAMPLE_POINTS):
    """Resample a polyline to n points evenly spaced by cumulative distance."""
    if len(points) < 2:
        return points * n if points else []

    # cumulative distances
    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(cum[-1] + haversine(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]))
    total = cum[-1]
    if total == 0:
        return [points[0]] * n

    targets = [total * i / (n - 1) for i in range(n)]
    result = []
    j = 0
    for t in targets:
        while j < len(cum) - 2 and cum[j + 1] < t:
            j += 1
        seg_len = cum[j + 1] - cum[j]
        frac = 0.0 if seg_len == 0 else (t - cum[j]) / seg_len
        lat = points[j][0] + frac * (points[j + 1][0] - points[j][0])
        lon = points[j][1] + frac * (points[j + 1][1] - points[j][1])
        result.append((lat, lon))
    return result


def _mean_deviation(a_resampled, b_resampled):
    return sum(
        haversine(a[0], a[1], b[0], b[1]) for a, b in zip(a_resampled, b_resampled)
    ) / len(a_resampled)


def tracks_match(a_resampled, a_len, b_resampled, b_len):
    if a_len == 0 or b_len == 0:
        return False
    if abs(a_len - b_len) / max(a_len, b_len) > MATCH_LENGTH_TOLERANCE:
        return False

    forward = _mean_deviation(a_resampled, b_resampled)
    reverse = _mean_deviation(a_resampled, list(reversed(b_resampled)))
    best = min(forward, reverse)
    return best <= MATCH_DISTANCE_THRESHOLD_M


class UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def rebuild_groups(db):
    """Recompute route groups from scratch based on all stored activities."""
    activities = db.query(Activity).all()
    if not activities:
        return

    # Precompute resampled points + lengths
    data = {}
    for act in activities:
        pts = act.resampled_points
        data[act.id] = (pts, act.distance_m or track_length(pts))

    uf = UnionFind([a.id for a in activities])

    for i in range(len(activities)):
        for j in range(i + 1, len(activities)):
            a, b = activities[i], activities[j]
            if (a.activity_type or "Other") != (b.activity_type or "Other"):
                continue  # don't group e.g. a run with a bike ride on the same road
            a_pts, a_len = data[a.id]
            b_pts, b_len = data[b.id]
            if tracks_match(a_pts, a_len, b_pts, b_len):
                uf.union(a.id, b.id)

    clusters = {}
    for act in activities:
        root = uf.find(act.id)
        clusters.setdefault(root, []).append(act)

    # Wipe existing groups, then recreate for clusters with 2+ activities
    for act in activities:
        act.group_id = None
    db.query(RouteGroup).delete()
    db.flush()

    for members in clusters.values():
        if len(members) < 2:
            continue  # ungrouped / unique route
        avg_dist = sum(data[m.id][1] for m in members) / len(members)
        group = RouteGroup(
            name=f"{avg_dist / 1000:.1f} km route",
            avg_distance_m=avg_dist,
        )
        db.add(group)
        db.flush()  # get group.id
        for m in members:
            m.group_id = group.id

    db.commit()
