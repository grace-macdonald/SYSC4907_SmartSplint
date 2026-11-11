"""JSON API used by the web pages (cookie session auth)."""

from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, abort, g, jsonify, request

from . import service
from .auth import clinician_required, login_required, require_participant_access
from .db import get_db, utcnow

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.errorhandler(service.ValidationError)
def _validation(exc):
    return jsonify(error=str(exc)), 422


def _participant_or_404(pid: str):
    require_participant_access(pid)
    row = service.get_participant(pid)
    if row is None:
        abort(404)
    return row


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise service.ValidationError("Expected a JSON object body.")
    return body


@bp.get("/me")
@login_required
def me():
    u = g.user
    return jsonify(username=u["username"], role=u["role"], participant_id=u["participant_id"])


# ---------------------------------------------------------------- participants

@bp.get("/participants")
@clinician_required
def participants():
    return jsonify(service.list_participants())


@bp.post("/participants")
@clinician_required
def create_participant():
    body = request.get_json(silent=True) or {}
    pid, password = service.create_participant(body_mass_kg=body.get("body_mass_kg"))
    return jsonify(participant_id=pid, password=password), 201


@bp.get("/participants/<pid>")
@login_required
def participant(pid):
    return jsonify(service.participant_view(_participant_or_404(pid)))


@bp.patch("/participants/<pid>")
@clinician_required
def update_participant(pid):
    _participant_or_404(pid)
    return jsonify(service.update_settings(pid, _json_body(), actor=g.user["username"]))


@bp.delete("/participants/<pid>")
@clinician_required
def delete_participant(pid):
    _participant_or_404(pid)
    service.delete_participant(pid)
    return jsonify(deleted=pid)


@bp.post("/participants/<pid>/consent")
@login_required
def consent(pid):
    _participant_or_404(pid)
    if g.user["participant_id"] != pid:
        abort(403)  # only the participant can consent for themselves
    if _json_body().get("agree") is not True:
        raise service.ValidationError("Consent requires agree: true.")
    service.give_consent(pid)
    return jsonify(service.participant_view(service.get_participant(pid)))


@bp.delete("/participants/<pid>/data")
@login_required
def delete_data(pid):
    _participant_or_404(pid)
    return jsonify(service.delete_participant_data(pid))


@bp.post("/participants/<pid>/estop")
@login_required
def estop(pid):
    """Log an app e-stop. The stop command itself goes straight to the brace over BLE."""
    _participant_or_404(pid)
    body = request.get_json(silent=True) or {}
    eid = service.record_event(pid, "safety", "app_estop",
                               {"by": g.user["username"], "ble_delivered": bool(body.get("ble_delivered")),
                                "ble_error": body.get("ble_error")})
    return jsonify(event_id=eid), 201


# ---------------------------------------------------------------------- events

@bp.get("/participants/<pid>/events")
@login_required
def events(pid):
    _participant_or_404(pid)
    cats = [c for c in request.args.get("category", "").split(",") if c]
    return jsonify(service.list_events(
        pid,
        after_id=request.args.get("after_id", 0, type=int),
        categories=cats or None,
        unacknowledged=request.args.get("open") == "1",
        limit=min(request.args.get("limit", 200, type=int), 1000),
    ))


@bp.post("/events/<int:eid>/acknowledge")
@clinician_required
def acknowledge(eid):
    db = get_db()
    row = db.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    if row is None:
        abort(404)
    if row["acknowledged_at"] is None:
        db.execute("UPDATE events SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
                   (utcnow(), g.user["username"], eid))
        db.commit()
    return jsonify(service.event_view(db.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()))


# -------------------------------------------------------------------- sessions

@bp.get("/participants/<pid>/sessions")
@login_required
def sessions(pid):
    _participant_or_404(pid)
    return jsonify(service.list_sessions(pid))


def _session_or_404(sid: int):
    row = service.get_session(sid)
    if row is None:
        abort(404)
    require_participant_access(row["participant_id"])
    return row


@bp.get("/sessions/<int:sid>")
@login_required
def session_detail(sid):
    row = _session_or_404(sid)
    max_points = min(request.args.get("max_points", 3000, type=int), 20000)
    return jsonify(
        session=dict(row),
        samples=service.get_samples(sid, max_points=max_points),
        events=[service.event_view(e) for e in get_db().execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY occurred_at", (sid,)).fetchall()],
    )


@bp.get("/sessions/<int:sid>/export.csv")
@login_required
def session_csv(sid):
    """Synchronised session log (FR-2). Empty cells are missing readings; valid=0 marks invalid frames."""
    row = _session_or_404(sid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["participant_id", "session_id", *service.SAMPLE_FIELDS])
    for s in service.get_samples(sid):
        w.writerow([row["participant_id"], sid, *("" if s[f] is None else s[f] for f in service.SAMPLE_FIELDS)])
    name = f"{row['participant_id']}_session{sid}_{row['started_at'][:10]}.csv"
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/participants/<pid>/sessions.csv")
@login_required
def sessions_csv(pid):
    _participant_or_404(pid)
    rows = service.list_sessions(pid)
    cols = ["id", "kind", "started_at", "ended_at", "support_level_pct", "reference_torque_nm",
            *service.SESSION_SUMMARY_FIELDS, "anomaly_count", "safety_count"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["participant_id", *cols])
    for r in rows:
        w.writerow([pid, *("" if r[c] is None else r[c] for c in cols)])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{pid}_sessions.csv"'})
