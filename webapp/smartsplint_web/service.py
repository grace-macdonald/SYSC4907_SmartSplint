"""Data operations shared by the API, the ingest endpoints, the CLI and the seeder."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app

from . import domain
from .db import get_db, utcnow


class ValidationError(ValueError):
    pass


# ---------------------------------------------------------------- participants

def next_participant_id(db: sqlite3.Connection) -> str:
    rows = db.execute("SELECT id FROM participants WHERE id LIKE 'P-%'").fetchall()
    nums = [int(r["id"][2:]) for r in rows if r["id"][2:].isdigit()]
    return f"P-{(max(nums) + 1) if nums else 1:03d}"


def create_participant(body_mass_kg: float | None = None, pid: str | None = None,
                       password: str | None = None) -> tuple[str, str]:
    """Create a participant and their login. Returns (participant_id, password)."""
    from .auth import create_user  # local import to avoid a cycle

    db = get_db()
    pid = pid or next_participant_id(db)
    if body_mass_kg is not None:
        _check_mass(body_mass_kg)
    password = password or secrets.token_urlsafe(9)
    db.execute(
        "INSERT INTO participants (id, created_at, body_mass_kg, rom_plantar_limit_deg, rom_dorsi_limit_deg) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, utcnow(), body_mass_kg, domain.DEFAULT_ROM_PLANTAR_DEG, domain.DEFAULT_ROM_DORSI_DEG),
    )
    db.commit()
    create_user(pid, password, "participant", pid)
    return pid, password


def get_participant(pid: str) -> sqlite3.Row | None:
    return get_db().execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()


def participant_view(row: sqlite3.Row) -> dict:
    """Participant settings plus the torque numbers derived from them."""
    safe = current_app.config["MOTOR_SAFE_TORQUE_NM"]
    mass = row["body_mass_kg"]
    out = dict(row)
    out.update(
        consented=row["consent_given_at"] is not None,
        full_support_torque_nm=domain.full_support_torque_nm(mass, safe),
        reference_torque_nm=domain.reference_torque_nm(row["support_level_pct"], mass, safe),
        torque_capped=domain.is_torque_capped(mass, safe),
        motor_safe_torque_nm=safe,
        motor_safe_torque_verified=current_app.config["MOTOR_SAFE_TORQUE_VERIFIED"],
    )
    return out


def list_participants() -> list[dict]:
    db = get_db()
    rows = db.execute(
        """
        SELECT p.*,
               (SELECT COUNT(*) FROM sessions s WHERE s.participant_id = p.id) AS session_count,
               (SELECT MAX(started_at) FROM sessions s WHERE s.participant_id = p.id) AS last_session_at,
               (SELECT COUNT(*) FROM events e WHERE e.participant_id = p.id
                    AND e.category IN ('anomaly', 'safety') AND e.acknowledged_at IS NULL) AS open_alerts,
               (SELECT COUNT(*) FROM events e WHERE e.participant_id = p.id
                    AND e.category = 'safety' AND e.acknowledged_at IS NULL) AS open_safety
        FROM participants p ORDER BY p.id
        """
    ).fetchall()
    out = []
    for r in rows:
        v = participant_view(r)
        v.update(session_count=r["session_count"], last_session_at=r["last_session_at"],
                 open_alerts=r["open_alerts"], open_safety=r["open_safety"])
        out.append(v)
    return out


def _check_mass(mass: object) -> float:
    if not isinstance(mass, (int, float)) or isinstance(mass, bool) or not 20 <= mass <= 250:
        raise ValidationError("Body mass must be a number between 20 and 250 kg.")
    return float(mass)


def update_settings(pid: str, changes: dict, actor: str) -> dict:
    """Clinician changes to body mass, support level and limits, with an audit event per change."""
    row = get_participant(pid)
    if row is None:
        raise LookupError(pid)
    current = dict(row)
    allowed = {"body_mass_kg", "support_level_pct", "support_max_pct", "weight_bearing_limit_kg",
               "rom_plantar_limit_deg", "rom_dorsi_limit_deg"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValidationError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    new = {**current, **changes}

    if "body_mass_kg" in changes:
        new["body_mass_kg"] = _check_mass(changes["body_mass_kg"])
    for key in ("support_level_pct", "support_max_pct"):
        if key in changes and not domain.is_valid_support_level(changes[key]):
            raise ValidationError("Support levels must be 0-100% in 5% steps.")
    if new["support_level_pct"] > new["support_max_pct"]:
        raise ValidationError(
            f"Support level {new['support_level_pct']}% is above this participant's "
            f"limit of {new['support_max_pct']}%. Raise the limit first.")
    if new["support_level_pct"] > 0 and new["body_mass_kg"] is None:
        raise ValidationError("Enter the participant's body mass before setting a support level.")
    wbl = new["weight_bearing_limit_kg"]
    if wbl is not None and (not isinstance(wbl, (int, float)) or isinstance(wbl, bool) or not 0 <= wbl <= 250):
        raise ValidationError("Weight-bearing limit must be between 0 and 250 kg, or empty for none.")
    # Post-op ROM limits may only tighten the mechanical hard stops (NFR-3, section 1.11).
    for key, hard in (("rom_plantar_limit_deg", domain.DEFAULT_ROM_PLANTAR_DEG),
                      ("rom_dorsi_limit_deg", domain.DEFAULT_ROM_DORSI_DEG)):
        v = new[key]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= hard:
            raise ValidationError(f"Range-of-motion limits must be between 0 and {hard:g} degrees "
                                  "(the brace's hard stop).")

    db = get_db()
    changed = {k: (current[k], new[k]) for k in allowed if current[k] != new[k]}
    if not changed:
        return participant_view(row)
    db.execute(
        "UPDATE participants SET body_mass_kg = ?, support_level_pct = ?, support_max_pct = ?, "
        "weight_bearing_limit_kg = ?, rom_plantar_limit_deg = ?, rom_dorsi_limit_deg = ? WHERE id = ?",
        (new["body_mass_kg"], new["support_level_pct"], new["support_max_pct"], new["weight_bearing_limit_kg"],
         new["rom_plantar_limit_deg"], new["rom_dorsi_limit_deg"], pid),
    )
    if "support_level_pct" in changed:
        old, val = changed.pop("support_level_pct")
        record_event(pid, "config", "support_level_changed", {"from": old, "to": val, "by": actor}, commit=False)
    if "body_mass_kg" in changed:
        old, val = changed.pop("body_mass_kg")
        record_event(pid, "config", "body_mass_changed", {"from": old, "to": val, "by": actor}, commit=False)
    if changed:
        record_event(pid, "config", "limits_changed",
                     {"changes": {k: {"from": a, "to": b} for k, (a, b) in changed.items()}, "by": actor},
                     commit=False)
    db.commit()
    return participant_view(get_participant(pid))


def give_consent(pid: str) -> None:
    db = get_db()
    db.execute("UPDATE participants SET consent_given_at = ?, consent_version = ? WHERE id = ?",
               (utcnow(), domain.CONSENT_VERSION, pid))
    record_event(pid, "config", "consent_given", {"version": domain.CONSENT_VERSION}, commit=False)
    db.commit()


def delete_participant_data(pid: str) -> dict:
    """Delete every session, sample and event for a participant and withdraw consent (FR-12).

    The participant record and login stay so the person can still sign in and
    see that nothing is held; the brace cannot upload again until consent is
    given again.
    """
    db = get_db()
    n_sessions = db.execute("SELECT COUNT(*) FROM sessions WHERE participant_id = ?", (pid,)).fetchone()[0]
    n_events = db.execute("SELECT COUNT(*) FROM events WHERE participant_id = ?", (pid,)).fetchone()[0]
    db.execute("DELETE FROM sessions WHERE participant_id = ?", (pid,))   # samples cascade
    db.execute("DELETE FROM events WHERE participant_id = ?", (pid,))
    db.execute("UPDATE participants SET consent_given_at = NULL, consent_version = NULL WHERE id = ?", (pid,))
    db.commit()
    return {"sessions_deleted": n_sessions, "events_deleted": n_events}


def delete_participant(pid: str) -> None:
    db = get_db()
    db.execute("DELETE FROM participants WHERE id = ?", (pid,))  # users, devices, sessions, events cascade
    db.commit()


def purge_expired(now: datetime | None = None) -> dict:
    """Delete sessions and events older than the retention period (section 1.12)."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=domain.RETENTION_DAYS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    db = get_db()
    s = db.execute("DELETE FROM sessions WHERE started_at < ?", (cutoff,)).rowcount
    e = db.execute("DELETE FROM events WHERE occurred_at < ?", (cutoff,)).rowcount
    db.commit()
    return {"sessions_deleted": s, "events_deleted": e, "cutoff": cutoff}


# ---------------------------------------------------------------------- events

def record_event(pid: str, category: str, type_: str, detail: dict | None = None,
                 session_id: int | None = None, occurred_at: str | None = None,
                 severity: str | None = None, commit: bool = True) -> int:
    if category not in domain.EVENT_TYPES or type_ not in domain.EVENT_TYPES[category]:
        raise ValidationError(f"Unknown event type {category}/{type_}")
    severity = severity or domain.EVENT_TYPES[category][type_][1]
    now = utcnow()
    db = get_db()
    cur = db.execute(
        "INSERT INTO events (participant_id, session_id, occurred_at, received_at, category, type, severity, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, session_id, occurred_at or now, now, category, type_, severity,
         json.dumps(detail) if detail is not None else None),
    )
    if commit:
        db.commit()
    return cur.lastrowid


def event_view(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["detail"] = json.loads(d["detail"]) if d["detail"] else None
    entry = domain.EVENT_TYPES.get(d["category"], {}).get(d["type"])
    d["label"] = entry[0] if entry else d["type"]
    d["patient_message"] = entry[2] if entry else None
    return d


def list_events(pid: str, *, after_id: int = 0, categories: list[str] | None = None,
                unacknowledged: bool = False, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM events WHERE participant_id = ? AND id > ?"
    args: list = [pid, after_id]
    if categories:
        sql += f" AND category IN ({','.join('?' * len(categories))})"
        args += categories
    if unacknowledged:  # only alerts can be open; config entries are an audit trail
        sql += " AND acknowledged_at IS NULL AND category IN ('anomaly', 'safety')"
    sql += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
    args.append(limit)
    return [event_view(r) for r in get_db().execute(sql, args).fetchall()]


# -------------------------------------------------------------------- sessions

SESSION_SUMMARY_FIELDS = (
    "strides", "cadence_spm", "stride_time_mean_s", "stride_time_cv_pct", "peak_plantar_load_kg",
    "push_off_torque_nm", "emg_peak_norm", "rom_deg", "assist_active_pct", "model_confidence_mean",
)

SAMPLE_FIELDS = (
    "t_ms", "fsr_heel", "fsr_medial", "fsr_lateral", "fsr_toe", "emg",
    "imu_foot_pitch", "imu_foot_gyro", "imu_shank_pitch", "imu_shank_gyro",
    "ankle_angle_deg", "hinge_torque_nm", "gait_phase", "phase_confidence", "valid",
)


def list_sessions(pid: str) -> list[dict]:
    rows = get_db().execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM events e WHERE e.session_id = s.id AND e.category = 'anomaly') AS anomaly_count,
               (SELECT COUNT(*) FROM events e WHERE e.session_id = s.id AND e.category = 'safety') AS safety_count,
               EXISTS (SELECT 1 FROM samples x WHERE x.session_id = s.id) AS has_samples
        FROM sessions s WHERE s.participant_id = ? ORDER BY s.started_at
        """, (pid,)).fetchall()
    return [dict(r) for r in rows]


def get_session(sid: int) -> sqlite3.Row | None:
    return get_db().execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()


def get_samples(sid: int, max_points: int | None = None) -> list[dict]:
    db = get_db()
    n = db.execute("SELECT COUNT(*) FROM samples WHERE session_id = ?", (sid,)).fetchone()[0]
    cols = ", ".join(SAMPLE_FIELDS)
    if max_points and n > max_points:
        step = -(-n // max_points)  # ceil
        rows = db.execute(
            f"SELECT {cols} FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY t_ms) - 1 AS rn "
            f"FROM samples WHERE session_id = ?) WHERE rn % ? = 0 ORDER BY t_ms", (sid, step)).fetchall()
    else:
        rows = db.execute(f"SELECT {cols} FROM samples WHERE session_id = ? ORDER BY t_ms", (sid,)).fetchall()
    return [dict(r) for r in rows]


def insert_samples(sid: int, samples: list[dict]) -> int:
    rows = []
    for i, s in enumerate(samples):
        if not isinstance(s, dict) or not isinstance(s.get("t_ms"), int):
            raise ValidationError(f"Sample {i}: each sample needs an integer t_ms.")
        phase = s.get("gait_phase")
        if phase not in (None, "stance", "push_off", "swing"):
            raise ValidationError(f"Sample {i}: gait_phase must be stance, push_off, swing or null.")
        rows.append(tuple([sid] + [s.get(f) if f != "valid" else int(s.get("valid", 1)) for f in SAMPLE_FIELDS]))
    db = get_db()
    placeholders = ",".join("?" * (len(SAMPLE_FIELDS) + 1))
    try:
        db.executemany(f"INSERT INTO samples (session_id, {', '.join(SAMPLE_FIELDS)}) VALUES ({placeholders})", rows)
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise ValidationError("Duplicate t_ms in this session.") from exc
    return len(rows)
