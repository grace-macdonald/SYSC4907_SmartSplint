"""Smart Splint web app: progress monitoring for participants and clinicians.

Run locally:
    flask --app smartsplint_web init-demo     # optional: synthetic demo data
    flask --app smartsplint_web run --debug
"""

from __future__ import annotations

import os
import secrets
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask, g, jsonify, request

from . import api, auth, db, ingest, pages, service


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SMARTSPLINT_SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.environ.get("SMARTSPLINT_DATABASE", str(Path(app.instance_path) / "smartsplint.db")),
        # The motor's verified safe torque caps 100% support (FR-6). PLACEHOLDER until the
        # bench test in P8 verifies it; set SMARTSPLINT_MOTOR_SAFE_TORQUE_NM once measured.
        MOTOR_SAFE_TORQUE_NM=float(os.environ.get("SMARTSPLINT_MOTOR_SAFE_TORQUE_NM", "50.0")),
        MOTOR_SAFE_TORQUE_VERIFIED=os.environ.get("SMARTSPLINT_MOTOR_SAFE_TORQUE_VERIFIED") == "1",
        # BLE identifiers for the app e-stop; must match the firmware's GATT service.
        BLE_SERVICE_UUID=os.environ.get("SMARTSPLINT_BLE_SERVICE_UUID", ""),
        BLE_ESTOP_CHAR_UUID=os.environ.get("SMARTSPLINT_BLE_ESTOP_CHAR_UUID", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Set SMARTSPLINT_SECURE_COOKIES=1 when served over HTTPS (always, outside local development).
        SESSION_COOKIE_SECURE=os.environ.get("SMARTSPLINT_SECURE_COOKIES") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if not os.environ.get("SMARTSPLINT_SECRET_KEY") and not app.testing:
        app.logger.warning("SMARTSPLINT_SECRET_KEY not set; using a random key (logins reset on restart).")

    db.init_app(app)
    app.before_request(auth.load_current_user)
    app.before_request(auth.check_csrf)
    app.register_blueprint(pages.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(ingest.bp)

    @app.context_processor
    def inject():
        return {"current_user": g.get("user"), "config": app.config}

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def json_errors(exc):
        if request.path.startswith("/api/"):
            return jsonify(error=exc.description), exc.code
        return exc

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    # Retention (section 1.12): purge on start-up; schedule `flask purge-expired` daily in production.
    with app.app_context():
        service.purge_expired()

    _register_cli(app)
    return app


def _register_cli(app: Flask) -> None:
    @app.cli.command("create-clinician")
    @click.argument("username")
    @click.password_option()
    def create_clinician(username, password):
        """Create a clinician / research-team login."""
        auth.create_user(username, password, "clinician")
        click.echo(f"Created clinician {username}")

    @app.cli.command("create-participant")
    @click.option("--mass", type=float, help="Body mass in kg")
    def create_participant(mass):
        """Create a participant ID and login (no names are stored)."""
        pid, password = service.create_participant(body_mass_kg=mass)
        click.echo(f"Participant ID: {pid}\nTemporary password: {password}")

    @app.cli.command("create-device")
    @click.argument("participant_id")
    @click.option("--label", default=None)
    def create_device(participant_id, label):
        """Issue an ingest token for a brace paired to PARTICIPANT_ID. Shown once."""
        if service.get_participant(participant_id) is None:
            raise click.ClickException(f"No participant {participant_id}")
        token = auth.new_device_token()
        conn = db.get_db()
        conn.execute("INSERT INTO devices (participant_id, label, token_hash, created_at) VALUES (?, ?, ?, ?)",
                     (participant_id, label, auth.hash_token(token), db.utcnow()))
        conn.commit()
        click.echo(f"Device token for {participant_id} (store it on the brace; it won't be shown again):\n{token}")

    @app.cli.command("purge-expired")
    def purge_expired():
        """Delete sessions and events older than 12 months."""
        click.echo(service.purge_expired())

    @app.cli.command("init-demo")
    @click.option("--reset", is_flag=True, help="Delete the existing database first")
    def init_demo(reset):
        """Load synthetic demo participants, sessions and alerts."""
        from .seed import seed_demo
        if reset:
            db.close_db()
            Path(app.config["DATABASE"]).unlink(missing_ok=True)
            db.init_db(db.get_db())
        click.echo(seed_demo())
