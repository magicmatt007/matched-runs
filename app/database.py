import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger("matched_runs")

DATA_DIR = "/data"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "gpx"), exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "app.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """Very small auto-migration: add any columns that exist in the ORM
    models but not yet in the actual sqlite table (SQLAlchemy's create_all()
    only creates missing tables, it never alters existing ones)."""
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    tables = {row[0] for row in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    def ensure_column(table, column, coltype_sql):
        if table not in tables:
            return
        existing_cols = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing_cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_sql}")
            conn.commit()

    ensure_column("strava_token", "scope", "VARCHAR")
    ensure_column("activities", "activity_type", "VARCHAR DEFAULT 'Other'")
    ensure_column("activities", "elevation_gain_m", "FLOAT")
    ensure_column("activities", "elevation_loss_m", "FLOAT")
    ensure_column("activities", "avg_heart_rate", "FLOAT")
    ensure_column("activities", "max_heart_rate", "FLOAT")
    ensure_column("activities", "avg_cadence", "FLOAT")
    ensure_column("activities", "calories", "FLOAT")
    ensure_column("activities", "elevation_profile_json", "TEXT")
    ensure_column("activities", "heart_rate_profile_json", "TEXT")
    ensure_column("activities", "time_profile_json", "TEXT")
    ensure_column("activities", "strava_activity_id", "VARCHAR")

    # File-based external_id previously stored the exact uploaded filename,
    # including any folder path (e.g. "export_12345/activities/123.gpx").
    # That path varies depending on how/where a file gets uploaded from -
    # a single re-uploaded file has no folder context, so it wouldn't match
    # the same activity from an original folder-based bulk import, silently
    # creating a duplicate instead of updating the existing row (confirmed
    # in practice). Normalizing existing rows to basename-only here, to
    # match what new uploads now store directly - see the upload route.
    if "activities" in tables:
        existing_cols = {row[1] for row in cur.execute("PRAGMA table_info(activities)").fetchall()}
        if "external_id" in existing_cols:
            rows = cur.execute(
                "SELECT id, source, external_id FROM activities WHERE source IN ('gpx','fit','tcx')"
            ).fetchall()
            # Track basenames already in use (source, basename) so a
            # normalization can't silently violate the uq_source_external_id
            # unique constraint if two different activities happen to
            # collide on the same basename (unlikely - Strava's own export
            # filenames are unique per file - but not impossible if files
            # were renamed or merged from multiple export batches).
            seen = set()
            for _id, source, ext_id in rows:
                seen.add((source, ext_id))
            for row_id, source, ext_id in rows:
                if not ext_id or "/" not in ext_id:
                    continue
                basename = ext_id.rsplit("/", 1)[-1]
                key = (source, basename)
                if key in seen and basename != ext_id:
                    logger.warning(
                        "[migration] skipping external_id normalization for activity %d "
                        "(%r -> %r) - another activity already uses that basename",
                        row_id, ext_id, basename,
                    )
                    continue
                cur.execute("UPDATE activities SET external_id = ? WHERE id = ?", (basename, row_id))
                seen.discard((source, ext_id))
                seen.add(key)
            conn.commit()

    conn.close()
