import json
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class RouteGroup(Base):
    __tablename__ = "route_groups"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    avg_distance_m = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    activities = relationship("Activity", back_populates="group", order_by="Activity.start_time")


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_source_external_id"),)

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)  # "gpx", "fit", "tcx", or "strava"
    external_id = Column(String, nullable=False)  # filename or strava activity id
    name = Column(String, default="Run")
    activity_type = Column(String, nullable=False, default="Other")  # e.g. Run, Ride, Hike, Walk
    start_time = Column(DateTime, nullable=True)
    distance_m = Column(Float, default=0.0)
    duration_s = Column(Float, nullable=True)

    # All nullable - not every source/file provides every field, and older
    # already-imported activities won't have these until re-synced/re-uploaded.
    elevation_gain_m = Column(Float, nullable=True)
    elevation_loss_m = Column(Float, nullable=True)
    avg_heart_rate = Column(Float, nullable=True)
    max_heart_rate = Column(Float, nullable=True)
    avg_cadence = Column(Float, nullable=True)
    calories = Column(Float, nullable=True)

    # Full track points (for drawing on the map), JSON list of [lat, lon]
    full_points_json = Column(Text, nullable=False)
    # Resampled fixed-length points used for matching, JSON list of [lat, lon]
    resampled_points_json = Column(Text, nullable=False)

    # Per-point series aligned with full_points (same length, null entries
    # where that point had no reading) - used for the elevation/heart rate
    # charts on the activity detail page. Only file-based imports (GPX/FIT/
    # TCX) currently populate these; Garmin/Strava live sync only exposes
    # summary totals (elevation_gain_m etc. above), not a per-point series,
    # from the endpoints this app calls.
    elevation_profile_json = Column(Text, nullable=True)
    heart_rate_profile_json = Column(Text, nullable=True)
    # Elapsed seconds since the activity's start, one per point (same
    # alignment as the two above) - not itself a "detail" shown directly,
    # but needed to derive pace at each point (distance and time between
    # consecutive points), which isn't recorded directly the way
    # elevation/heart rate are.
    time_profile_json = Column(Text, nullable=True)
    # The real Strava activity ID, backfilled from the "Activity ID"
    # column in Strava's own activities.csv bulk export - deliberately
    # NOT derived from the exported filename (confirmed via direct
    # testing that a filename-based guess produces links to entirely
    # different people's activities). Only ever populated for file-based
    # imports; live Strava sync already has the real ID in external_id.
    strava_activity_id = Column(String, nullable=True)

    group_id = Column(Integer, ForeignKey("route_groups.id"), nullable=True)
    group = relationship("RouteGroup", back_populates="activities")

    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def full_points(self):
        return json.loads(self.full_points_json)

    @property
    def resampled_points(self):
        return json.loads(self.resampled_points_json)

    @property
    def elevation_profile(self):
        return json.loads(self.elevation_profile_json) if self.elevation_profile_json else None

    @property
    def heart_rate_profile(self):
        return json.loads(self.heart_rate_profile_json) if self.heart_rate_profile_json else None

    @property
    def time_profile(self):
        return json.loads(self.time_profile_json) if self.time_profile_json else None

    @property
    def external_activity_url(self):
        """Link to this activity on the original service's website, if
        one can be determined - None otherwise.

        For live Garmin/Strava sync, external_id is already the numeric
        activity ID used directly in that service's own activity URLs.

        For a file-based import, only strava_activity_id (backfilled from
        the "Activity ID" column in Strava's own activities.csv, if that
        was uploaded) is used. An earlier version of this tried treating a
        purely-numeric filename as the activity ID directly, on the theory
        that activities.csv's own Activity ID column couldn't be trusted -
        that theory was wrong: confirmed via direct testing that the
        filename-based guess produced links to other people's activities
        entirely, while activities.csv's Activity ID column is correct.
        """
        if self.source == "garmin" and self.external_id:
            return f"https://connect.garmin.com/modern/activity/{self.external_id}"
        if self.source == "strava" and self.external_id:
            return f"https://www.strava.com/activities/{self.external_id}"
        if self.strava_activity_id:
            return f"https://www.strava.com/activities/{self.strava_activity_id}"
        return None


class StravaToken(Base):
    """Single-row table holding the OAuth token for the (single) app user."""
    __tablename__ = "strava_token"

    id = Column(Integer, primary_key=True)
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=False)
    expires_at = Column(Integer, nullable=False)  # unix timestamp
    athlete_id = Column(String, nullable=True)
    scope = Column(String, nullable=True)


class GarminSyncState(Base):
    """Single-row table tracking how far Garmin sync has checked, independent
    of which activities were actually importable. Without this, activities
    with no GPS data (strength training, indoor workouts, etc.) would get
    re-fetched and re-attempted on every single sync forever, since a failed
    import never advances a "last successfully imported" watermark."""
    __tablename__ = "garmin_sync_state"

    id = Column(Integer, primary_key=True)
    last_checked_at = Column(DateTime, nullable=True)
