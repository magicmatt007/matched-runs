import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

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

    conn.close()
