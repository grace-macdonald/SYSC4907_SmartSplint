"""Device-facing API: the brace (or the phone relaying for it) uploads data here.

Every request carries `Authorization: Bearer <device token>`. A token is bound to
one participant, so a device can only ever write to that participant's record.
Nothing is stored until the participant has given consent (FR-12).
"""

from __future__ import annotations

from flask import Blueprint, abort, current_app, g, jsonify, request

from . import domain, service
from .auth import device_required
from .db import get_db

bp = Blueprint("ingest", __name__, url_prefix="/api/ingest")


@bp.errorhandler(service.ValidationError)
def _validation(exc):
    return jsonify(error=str(exc)), 422


def _require_consent():
    p = service.get_participant(g.device["participant_id"])
    if p is None or p["consent_given_at"] is None:
        abort(403, description="Participant has not given consent; data will not be stored.")
    return p


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise service.ValidationError("Expected a JSON object body.")
    return body


@bp.get("/config")
@device_required
def config():
    """Settings the brace needs: support level, reference torque and limits."""
    p = service.participant_view(service.get_participant(g.device["participant_id"]))
    keys = ("id", "consented", "body_mass_kg", "support_level_pct", "full_support_torque_nm",
            "reference_torque_nm", "torque_capped", "weight_bearing_limit_kg",
            "rom_plantar_limit_deg", "rom_dorsi_limit_deg")
    return jsonify({k: p[k] for k in keys} | {"participant_id": p["id"]})


@bp.post("/sessions")
@device_required
def create_session():
    p = _require_consent()
    body = _json_body()
    if not isinstance(body.get("started_at"), str):
        raise service.ValidationError("started_at (ISO-8601 string) is required.")
    kind = body.get("kind", "walk")
    if kind not in ("walk", "calibration", "bench"):
        raise service.ValidationError("kind must be walk, calibration or bench.")
    level = body.get("support_level_pct", p["support_level_pct"])
    if not domain.is_valid_support_level(level):
        raise service.ValidationError("support_level_pct must be 0-100 in 5% steps.")
    safe = current_app.config["MOTOR_SAFE_TORQUE_NM"]
    ref = body.get("reference_torque_nm", domain.reference_torque_nm(level, p["body_mass_kg"], safe))

    summary = {f: body.get(f) for f in service.SESSION_SUMMARY_FIELDS}
    db = get_db()
    cur = db.execute(
        f"INSERT INTO sessions (participant_id, kind, started_at, ended_at, support_level_pct, reference_torque_nm, "
        f"{', '.join(summary)}, notes) VALUES (?, ?, ?, ?, ?, ?, {', '.join('?' * len(summary))}, ?)",
        (p["id"], kind, body["started_at"], body.get("ended_at"), level, ref, *summary.values(), body.get("notes")),
    )
    sid = cur.lastrowid
    n = service.insert_samples(sid, body.get("samples") or [])
    db.commit()
    return jsonify(session_id=sid, samples_stored=n), 201


@bp.patch("/sessions/<int:sid>")
@device_required
def update_session(sid):
    """Fill in ended_at and summary metrics once the session is processed."""
    _require_consent()
    row = service.get_session(sid)
    if row is None or row["participant_id"] != g.device["participant_id"]:
        abort(404)
    body = _json_body()
    fields = [f for f in ("ended_at", "notes", *service.SESSION_SUMMARY_FIELDS) if f in body]
    if fields:
        db = get_db()
        db.execute(f"UPDATE sessions SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                   (*(body[f] for f in fields), sid))
        db.commit()
    return jsonify(dict(service.get_session(sid)))


@bp.post("/sessions/<int:sid>/samples")
@device_required
def append_samples(sid):
    """Append a chunk of samples to an open session."""
    _require_consent()
    row = service.get_session(sid)
    if row is None or row["participant_id"] != g.device["participant_id"]:
        abort(404)
    samples = _json_body().get("samples")
    if not isinstance(samples, list):
        raise service.ValidationError("samples must be a list.")
    n = service.insert_samples(sid, samples)
    get_db().commit()
    return jsonify(samples_stored=n), 201


@bp.post("/events")
@device_required
def post_events():
    """Anomaly alerts (P9) and safe-state entries (FR-11) from the brace or the cloud checker."""
    p = _require_consent()
    events = _json_body().get("events")
    if not isinstance(events, list) or not events:
        raise service.ValidationError("events must be a non-empty list.")
    ids = []
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            raise service.ValidationError(f"Event {i} must be an object.")
        sid = e.get("session_id")
        if sid is not None:
            s = service.get_session(sid)
            if s is None or s["participant_id"] != p["id"]:
                raise service.ValidationError(f"Event {i}: unknown session_id {sid}.")
        if e.get("category") == "config":
            raise service.ValidationError(f"Event {i}: devices cannot post config events.")
        try:
            ids.append(service.record_event(p["id"], e.get("category"), e.get("type"), e.get("detail"),
                                            session_id=sid, occurred_at=e.get("occurred_at"), commit=False))
        except service.ValidationError as exc:
            get_db().rollback()
            raise service.ValidationError(f"Event {i}: {exc}") from exc
    get_db().commit()
    return jsonify(event_ids=ids), 201
