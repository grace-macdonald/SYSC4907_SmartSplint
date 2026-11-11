"""Login, role checks, and device-token authentication.

Two roles:
  * clinician   - research team / physiotherapist / surgeon. Sees every
                  participant, sets support levels and limits, acknowledges alerts.
  * participant - the wearer. Logs in with their participant ID and sees only
                  their own data.

Braces (or the phone relaying for one) use a bearer token issued per device.
"""

from __future__ import annotations

import hashlib
import secrets
from functools import wraps
from typing import Callable

from flask import abort, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, utcnow


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_device_token() -> str:
    return "ss_" + secrets.token_urlsafe(32)


def create_user(username: str, password: str, role: str, participant_id: str | None = None) -> int:
    db = get_db()
    cur = db.execute(
        "INSERT INTO users (username, password_hash, role, participant_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, hash_password(password), role, participant_id, utcnow()),
    )
    db.commit()
    return cur.lastrowid


def authenticate(username: str, password: str):
    row = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        return None
    return row


def load_current_user() -> None:
    g.user = None
    uid = session.get("uid")
    if uid is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None:
            session.clear()


def _wants_json() -> bool:
    return request.path.startswith("/api/")


def login_required(view: Callable) -> Callable:
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            if _wants_json():
                return jsonify(error="Not signed in"), 401
            from flask import redirect, url_for
            return redirect(url_for("pages.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def clinician_required(view: Callable) -> Callable:
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "clinician":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def can_view_participant(pid: str) -> bool:
    user = g.get("user")
    if user is None:
        return False
    return user["role"] == "clinician" or user["participant_id"] == pid


def require_participant_access(pid: str) -> None:
    if not can_view_participant(pid):
        # 404 rather than 403 so participant IDs can't be probed.
        abort(404)


def check_csrf() -> None:
    """Cookie-authenticated writes must carry a custom header.

    Browsers won't add a custom header to a cross-site request without a CORS
    preflight, which this app never grants, so this blocks CSRF for the JSON
    API. Device ingest uses bearer tokens and is exempt.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.path.startswith("/api/ingest/"):
        return
    if request.path.startswith("/api/") and request.headers.get("X-Requested-With") != "smartsplint":
        abort(400, description="Missing X-Requested-With header")


def device_required(view: Callable) -> Callable:
    """Authenticate a brace by `Authorization: Bearer <token>`; sets g.device."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify(error="Missing device token"), 401
        row = get_db().execute(
            "SELECT * FROM devices WHERE token_hash = ? AND revoked_at IS NULL",
            (hash_token(header[7:].strip()),),
        ).fetchone()
        if row is None:
            return jsonify(error="Invalid or revoked device token"), 401
        g.device = row
        return view(*args, **kwargs)
    return wrapped
