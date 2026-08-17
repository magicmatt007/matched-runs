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

The point-deviation threshold in step 2 is `max(MATCH_DISTANCE_THRESHOLD_M,
route_length * MATCH_DISTANCE_TOLERANCE)` - a flat floor PLUS a component
that scales with route length, not a single flat number. A long route
naturally accumulates more absolute GPS/riding-line deviation between two
otherwise-identical recordings than a short one does (more turns, more GPS
noise exposure, more real minor route choices like which side of a wide
road) - confirmed directly against real data: a flat threshold loose
enough to reliably match a ~94km ride's repeats (needed ~500m in practice)
was, at that same absolute value, loose enough to risk merging genuinely
different short routes that happen to start nearby (500m is already 17%
of a 3km loop). The 40 resampled points are spaced further apart in
absolute terms for a longer route too (roughly route_length / 39), which
is the same underlying reason a fixed-count resampling needs a
length-aware comparison threshold to remain equally strict at every
scale. Verified directly against a real ~94km route matched at four
different times (genuine same-route deviation: 0.18%-0.43% of route
length) against six different real short/medium/long route *pairs* that
must NOT match (2.9%-1392% of route length, at least 5x this fix's
default margin above the wanted case) - see CHANGELOG.md for the exact
numbers.
"""
import math
import os
import json
from datetime import timedelta
from app.models import Activity, RouteGroup

RESAMPLE_POINTS = 40

MATCH_DISTANCE_THRESHOLD_M = float(os.environ.get("MATCH_DISTANCE_THRESHOLD_M", 85))
MATCH_DISTANCE_TOLERANCE = float(os.environ.get("MATCH_DISTANCE_TOLERANCE", 0.006))
MATCH_LENGTH_TOLERANCE = float(os.environ.get("MATCH_LENGTH_TOLERANCE", 0.15))

# When the same real-world activity gets imported from more than one source
# (e.g. a bulk Strava export AND live Garmin sync both bringing in the same
# hike), prefer whichever source has richer metadata - live API syncs give
# you the actual title/type, raw file uploads often just have a filename.
SOURCE_PRIORITY = {"garmin": 2, "strava": 2, "gpx": 1, "fit": 1, "tcx": 1}

# How close two activities' start times need to be to even consider them the
# same real-world activity (route geometry still has to match too).
DUPLICATE_TIME_WINDOW = timedelta(minutes=20)


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
    # A flat floor (MATCH_DISTANCE_THRESHOLD_M) PLUS a component that
    # scales with route length - see this module's docstring for why a
    # single flat number can't be both strict enough for a short loop and
    # loose enough for a long ride at the same time.
    effective_threshold = max(MATCH_DISTANCE_THRESHOLD_M, max(a_len, b_len) * MATCH_DISTANCE_TOLERANCE)
    return best <= effective_threshold


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


def find_cross_source_duplicate(db, points, distance_m, start_time):
    """Look for an already-imported activity that's almost certainly the
    same real-world activity: start time close together and matching
    route geometry. Returns the existing Activity row, or None.
    Activities with no GPS track (e.g. indoor swims) are never compared
    this way - there's no route to match, and it's not safe to compare two
    empty tracks (see tracks_match/_mean_deviation).

    Deliberately does NOT exclude same-`source` activities (an earlier
    version did, hence this function's name - kept as-is, "cross-source"
    is still the common case this matters for). `source` is set by file
    FORMAT for a file-based import (fit/gpx/tcx), not by which SERVICE
    delivered it - a Garmin export and a Strava export of the exact same
    real activity both land on source="tcx" (or fit/gpx) if both happen
    to be that format, which used to make this function - and the "Merge
    duplicate activities" button, which shares this same source != source
    requirement - silently unable to ever catch that very common case.
    Confirmed directly: a real Garmin-exported and Strava-exported TCX of
    the same hike, identical start_time and distance to the millimeter,
    sat as two permanently unmerged rows because both were source="tcx"."""
    if start_time is None or not points:
        return None

    candidates = (
        db.query(Activity)
        .filter(Activity.start_time >= start_time - DUPLICATE_TIME_WINDOW)
        .filter(Activity.start_time <= start_time + DUPLICATE_TIME_WINDOW)
        .all()
    )
    candidates = [c for c in candidates if c.resampled_points]
    if not candidates:
        return None

    new_resampled = resample_track(points)
    new_len = distance_m or track_length(points)

    for c in candidates:
        c_pts = c.resampled_points
        c_len = c.distance_m or track_length(c_pts)
        if tracks_match(new_resampled, new_len, c_pts, c_len):
            return c
    return None


def merge_duplicate_activities(db):
    """One-off retroactive cleanup: scan everything already imported for
    duplicates (same route, close start time) and merge them down to a
    single row, keeping whichever source ranks higher in SOURCE_PRIORITY
    (ties - e.g. two file-based imports - keep whichever comes first by
    start_time, arbitrary but deterministic). Returns the number of
    duplicate rows removed.

    Deliberately does NOT skip a same-`source` pair (an earlier version
    did) - see find_cross_source_duplicate's docstring for exactly why
    that was wrong: `source` reflects file FORMAT for a file-based
    import, not which SERVICE it came from, so a Garmin export and a
    Strava export of the same real activity commonly share a source
    string and were - confirmed directly - never merged by this
    function as a result, even after clicking this exact button."""
    activities = db.query(Activity).order_by(Activity.start_time).all()
    to_delete = set()

    for i in range(len(activities)):
        a = activities[i]
        if a.id in to_delete or a.start_time is None or not a.resampled_points:
            continue
        for j in range(i + 1, len(activities)):
            b = activities[j]
            if b.id in to_delete or b.start_time is None or not b.resampled_points:
                continue
            if b.start_time - a.start_time > DUPLICATE_TIME_WINDOW:
                break  # activities are ordered by start_time, nothing further can be within the window
            a_pts, b_pts = a.resampled_points, b.resampled_points
            a_len = a.distance_m or track_length(a_pts)
            b_len = b.distance_m or track_length(b_pts)
            if not tracks_match(a_pts, a_len, b_pts, b_len):
                continue
            keep, drop = (a, b) if SOURCE_PRIORITY.get(a.source, 0) >= SOURCE_PRIORITY.get(b.source, 0) else (b, a)
            to_delete.add(drop.id)

    if to_delete:
        db.query(Activity).filter(Activity.id.in_(to_delete)).delete(synchronize_session=False)
        db.commit()
        rebuild_groups(db)

    return len(to_delete)


def _seeded_clusters(candidate_ids, existing_group_of, new_ids, get_type, get_track, match_fn):
    """
    Core clustering logic, kept dependency-free (no DB) so it's directly
    testable. Given a pool of candidate activity ids:
    - seeds a union-find from their EXISTING group memberships (cheap - no
      geometry comparison, since those groupings are already known-correct)
    - then only computes NEW pairwise geometry comparisons for pairs
      involving at least one id in `new_ids` (covers new-vs-existing and
      new-vs-new; skips existing-vs-existing entirely, and skips any pair
      already unioned via the seed step)
    Returns {id: cluster_root_id}.

    get_type(id) -> activity type string
    get_track(id) -> (resampled_points, length_m)
    match_fn(a_pts, a_len, b_pts, b_len) -> bool
    """
    uf = UnionFind(candidate_ids)

    by_group = {}
    for cid in candidate_ids:
        g = existing_group_of.get(cid)
        if g:
            by_group.setdefault(g, []).append(cid)
    for members in by_group.values():
        for other in members[1:]:
            uf.union(members[0], other)

    for nid in new_ids:
        n_pts, n_len = get_track(nid)
        n_type = get_type(nid)
        for cid in candidate_ids:
            if cid == nid or uf.find(nid) == uf.find(cid):
                continue
            if get_type(cid) != n_type:
                continue
            c_pts, c_len = get_track(cid)
            if match_fn(n_pts, n_len, c_pts, c_len):
                uf.union(nid, cid)

    return {cid: uf.find(cid) for cid in candidate_ids}


def incremental_rebuild_groups(db, new_activity_ids):
    """
    Cheaper alternative to rebuild_groups() when only a small number of new
    activities were just imported into a large existing history (e.g. a
    routine Garmin sync bringing in 1-2 new runs). Produces the same
    clustering result rebuild_groups() would, but only computes NEW
    pairwise geometry comparisons instead of recomputing every pair in the
    whole database - existing, already-correct group memberships are reused
    via seeding rather than re-derived. Falls back to a full rebuild_groups()
    if there's no existing grouping yet to build on (e.g. first import ever).
    """
    if not new_activity_ids:
        return

    new_activities = [a for a in db.query(Activity).filter(Activity.id.in_(new_activity_ids)).all()]
    if not new_activities:
        return

    if db.query(RouteGroup.id).first() is None:
        # Nothing to incrementally build on yet - a full rebuild is simplest
        # and no more expensive than this would be in that case anyway.
        rebuild_groups(db)
        return

    relevant_types = {(a.activity_type or "Other") for a in new_activities}

    # Candidate pool: every activity (new or existing) sharing a type with
    # at least one new activity - matching can only happen within the same
    # type, so anything outside these types is correctly never touched.
    # Activities with no GPS track are excluded entirely - see
    # rebuild_groups for why (no route to match, and comparing two empty
    # tracks would risk a divide-by-zero).
    candidates = [
        a for a in db.query(Activity).filter(Activity.activity_type.in_(relevant_types)).all()
        if a.resampled_points
    ]
    by_id = {a.id: a for a in candidates}
    tracks = {}
    for act in candidates:
        pts = act.resampled_points
        tracks[act.id] = (pts, act.distance_m or track_length(pts))

    new_ids = {a.id for a in new_activities if a.id in by_id}
    existing_group_of = {a.id: a.group_id for a in candidates if a.group_id}

    clusters_by_root = {}
    result = _seeded_clusters(
        candidate_ids=list(by_id.keys()),
        existing_group_of=existing_group_of,
        new_ids=new_ids,
        get_type=lambda i: by_id[i].activity_type or "Other",
        get_track=lambda i: tracks[i],
        match_fn=tracks_match,
    )
    for cid, root in result.items():
        clusters_by_root.setdefault(root, []).append(cid)

    # Only touch RouteGroup rows that fall within this candidate pool -
    # anything outside these types is untouched and doesn't need locking.
    affected_group_ids = set(existing_group_of.values())
    for act in candidates:
        act.group_id = None
    if affected_group_ids:
        db.query(RouteGroup).filter(RouteGroup.id.in_(affected_group_ids)).delete(synchronize_session=False)
    db.flush()

    for member_ids in clusters_by_root.values():
        if len(member_ids) < 2:
            continue  # ungrouped / unique route
        avg_dist = sum(tracks[i][1] for i in member_ids) / len(member_ids)
        group = RouteGroup(
            name=f"{avg_dist / 1000:.1f} km route",
            avg_distance_m=avg_dist,
        )
        db.add(group)
        db.flush()
        for i in member_ids:
            by_id[i].group_id = group.id

    db.commit()


def rebuild_groups(db, progress_callback=None):
    """Recompute route groups from scratch based on all stored activities.

    progress_callback(done, total), if given, is called periodically (not
    on every single comparison - that would be needless overhead for
    what's often a million+ iteration loop) with how many of the O(n^2)
    pairwise comparisons have been completed so far."""
    all_activities = db.query(Activity).all()
    if not all_activities:
        return

    # Activities with no GPS track (e.g. indoor swims, gym workouts) can
    # never be route-matched - exclude them from the matching graph
    # entirely. They just stay ungrouped, same as any activity that didn't
    # match anything; comparing two empty tracks against each other would
    # otherwise risk a divide-by-zero (see tracks_match/_mean_deviation).
    activities = [a for a in all_activities if a.resampled_points]
    for a in all_activities:
        if not a.resampled_points:
            a.group_id = None

    # Precompute resampled points + lengths
    data = {}
    for act in activities:
        pts = act.resampled_points
        data[act.id] = (pts, act.distance_m or track_length(pts))

    uf = UnionFind([a.id for a in activities])

    n = len(activities)
    total_comparisons = n * (n - 1) // 2
    done_comparisons = 0
    report_every = max(1, total_comparisons // 200)  # ~200 updates over the whole run

    for i in range(len(activities)):
        for j in range(i + 1, len(activities)):
            a, b = activities[i], activities[j]
            if (a.activity_type or "Other") != (b.activity_type or "Other"):
                continue  # don't group e.g. a run with a bike ride on the same road
            a_pts, a_len = data[a.id]
            b_pts, b_len = data[b.id]
            if tracks_match(a_pts, a_len, b_pts, b_len):
                uf.union(a.id, b.id)
            done_comparisons += 1
            if progress_callback and done_comparisons % report_every == 0:
                progress_callback(done_comparisons, total_comparisons)

    if progress_callback:
        progress_callback(total_comparisons, total_comparisons)

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
