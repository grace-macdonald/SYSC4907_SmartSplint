"""HTML pages. Each page is a thin template; the data is loaded by its script from the JSON API."""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from . import domain, service
from .auth import authenticate, clinician_required, login_required, require_participant_access

bp = Blueprint("pages", __name__)


def _safe_next(target: str | None) -> str | None:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = authenticate(request.form.get("username", "").strip(), request.form.get("password", ""))
        if user is None:
            flash("That participant ID / username and password don't match.")
            return render_template("login.html"), 401
        session.clear()
        session["uid"] = user["id"]
        session.permanent = True
        return redirect(_safe_next(request.args.get("next")) or url_for("pages.home"))
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("pages.login"))


@bp.get("/")
@login_required
def home():
    if g.user["role"] == "clinician":
        return redirect(url_for("pages.roster"))
    return redirect(url_for("pages.my_progress"))


# ------------------------------------------------------------------ participant

@bp.route("/consent", methods=["GET", "POST"])
@login_required
def consent():
    if g.user["role"] != "participant":
        return redirect(url_for("pages.home"))
    pid = g.user["participant_id"]
    if request.method == "POST":
        if request.form.get("agree") == "yes":
            service.give_consent(pid)
            return redirect(url_for("pages.my_progress"))
        flash("Tick the box to give consent, or close this page if you don't want to take part.")
    return render_template("consent.html", participant=service.get_participant(pid),
                           retention_days=domain.RETENTION_DAYS, consent_version=domain.CONSENT_VERSION)


@bp.get("/me")
@login_required
def my_progress():
    if g.user["role"] != "participant":
        return redirect(url_for("pages.home"))
    p = service.get_participant(g.user["participant_id"])
    if p["consent_given_at"] is None:
        return redirect(url_for("pages.consent"))
    return render_template("patient.html", pid=p["id"])


# -------------------------------------------------------------------- clinician

@bp.get("/clinician")
@clinician_required
def roster():
    return render_template("roster.html")


@bp.get("/clinician/participants/<pid>")
@clinician_required
def participant(pid):
    if service.get_participant(pid) is None:
        abort(404)
    return render_template("participant.html", pid=pid)


# ---------------------------------------------------------------------- shared

@bp.get("/sessions/<int:sid>")
@login_required
def session_page(sid):
    row = service.get_session(sid)
    if row is None:
        abort(404)
    require_participant_access(row["participant_id"])
    return render_template("session.html", sid=sid, pid=row["participant_id"])
