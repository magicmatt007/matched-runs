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

    # Full track points (for drawing on the map), JSON list of [lat, lon]
    full_points_json = Column(Text, nullable=False)
    # Resampled fixed-length points used for matching, JSON list of [lat, lon]
    resampled_points_json = Column(Text, nullable=False)

    group_id = Column(Integer, ForeignKey("route_groups.id"), nullable=True)
    group = relationship("RouteGroup", back_populates="activities")

    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def full_points(self):
        return json.loads(self.full_points_json)

    @property
    def resampled_points(self):
        return json.loads(self.resampled_points_json)


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
