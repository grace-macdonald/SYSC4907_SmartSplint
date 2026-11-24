"""SQLite access. One connection per request, stored on flask.g."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, current_app, g

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    """ISO-8601 UTC timestamp with millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(conn: sqlite3.Connection) -> None:
    _migrate(conn)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases from the first web app version up to the 5-FSR insole layout.

    The first version guessed four insole regions (fsr_heel, fsr_medial, fsr_lateral,
    fsr_toe) on a 0-1 scale. Those were only ever demo values, so old rows are kept
    but moved to the closest new sensor; reload the demo with `init-demo --reset`
    to get realistic raw ADC values.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(samples)").fetchall()}
    if "fsr_heel" not in cols:
        return
    for old, new in (("fsr_toe", "fsr1"), ("fsr_medial", "fsr2"), ("fsr_lateral", "fsr4"), ("fsr_heel", "fsr5")):
        conn.execute(f"ALTER TABLE samples RENAME COLUMN {old} TO {new}")
    for new in ("fsr3", "fsr6"):
        conn.execute(f"ALTER TABLE samples ADD COLUMN {new} INTEGER")
    conn.commit()


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    with app.app_context():
        init_db(get_db())


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
