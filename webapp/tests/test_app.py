"""Tests for the rules the proposal puts on the web app (FR-2, FR-6, FR-12, NFR-11, NFR-13, section 1.12)."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from smartsplint_web import auth, create_app, domain, service
from smartsplint_web.db import get_db

H = {"X-Requested-With": "smartsplint"}


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db"), "SECRET_KEY": "test",
                      "MOTOR_SAFE_TORQUE_NM": 50.0})
    with app.app_context():
        auth.create_user("doc", "pw", "clinician")
        service.create_participant(body_mass_kg=70.0, pid="P-001", password="p1")
        service.create_participant(body_mass_kg=95.0, pid="P-002", password="p2")
        conn = get_db()
        conn.execute("UPDATE participants SET support_max_pct = 60")
        conn.commit()
    return app


def login(app, user, pw):
    c = app.test_client()
    r = c.post("/login", data={"username": user, "password": pw})
    assert r.status_code == 302, r.data
    return c


def device_token(app, pid):
    token = auth.new_device_token()
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO devices (participant_id, token_hash, created_at) VALUES (?, ?, ?)",
                     (pid, auth.hash_token(token), "2026-11-01T00:00:00Z"))
        conn.commit()
    return {"Authorization": f"Bearer {token}"}


def consent(app, pid):
    with app.app_context():
        service.give_consent(pid)


# ------------------------------------------------------------------ auth & access

def test_bad_login_rejected(app):
    r = app.test_client().post("/login", data={"username": "doc", "password": "nope"})
    assert r.status_code == 401


def test_api_requires_login(app):
    assert app.test_client().get("/api/participants/P-001").status_code == 401


def test_participant_sees_only_own_data(app):
    c = login(app, "P-001", "p1")
    assert c.get("/api/participants/P-001").status_code == 200
    assert c.get("/api/participants/P-002").status_code == 404
    assert c.get("/api/participants/P-002/sessions").status_code == 404
    assert c.get("/api/participants").status_code == 403


def test_participant_cannot_change_support(app):
    c = login(app, "P-001", "p1")
    r = c.patch("/api/participants/P-001", json={"support_level_pct": 50}, headers=H)
    assert r.status_code == 403


def test_writes_need_csrf_header(app):
    c = login(app, "doc", "pw")
    assert c.patch("/api/participants/P-001", json={"support_level_pct": 10}).status_code == 400
    assert c.patch("/api/participants/P-001", json={"support_level_pct": 10}, headers=H).status_code == 200


def test_no_names_in_schema(app):
    with app.app_context():
        cols = {r["name"] for t in ("participants", "users")
                for r in get_db().execute(f"PRAGMA table_info({t})").fetchall()}
    assert not {"name", "first_name", "last_name", "full_name", "email"} & cols


# ------------------------------------------------------------------ support level (FR-6)

def test_torque_scaling_and_cap():
    assert domain.full_support_torque_nm(70, 50) == pytest.approx(42.7)
    assert domain.full_support_torque_nm(95, 50) == 50  # 95 * 0.61 = 57.95 -> capped
    assert domain.reference_torque_nm(25, 70, 50) == pytest.approx(10.68, abs=0.01)
    assert domain.is_torque_capped(95, 50) and not domain.is_torque_capped(70, 50)


@pytest.mark.parametrize("body,msg", [
    ({"support_level_pct": 12}, "5% steps"),
    ({"support_level_pct": 105}, "5% steps"),
    ({"support_level_pct": True}, "5% steps"),
    ({"support_level_pct": 65}, "above this participant's limit"),
    ({"rom_dorsi_limit_deg": 20}, "hard stop"),
    ({"body_mass_kg": 5}, "Body mass"),
    ({"surname": "x"}, "Unknown field"),
])
def test_support_validation(app, body, msg):
    c = login(app, "doc", "pw")
    r = c.patch("/api/participants/P-001", json=body, headers=H)
    assert r.status_code == 422
    assert msg in r.get_json()["error"]


def test_support_change_is_audited(app):
    c = login(app, "doc", "pw")
    r = c.patch("/api/participants/P-001", json={"support_level_pct": 40}, headers=H)
    assert r.status_code == 200
    body = r.get_json()
    assert body["reference_torque_nm"] == pytest.approx(17.08, abs=0.01)
    evs = c.get("/api/participants/P-001/events?category=config").get_json()
    assert evs[0]["type"] == "support_level_changed"
    assert evs[0]["detail"] == {"from": 0, "to": 40, "by": "doc"}


# ------------------------------------------------------------------ ingest & consent (FR-12)

def test_ingest_requires_token(app):
    c = app.test_client()
    assert c.post("/api/ingest/sessions", json={}).status_code == 401
    assert c.post("/api/ingest/sessions", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_ingest_blocked_without_consent(app):
    dev = device_token(app, "P-001")
    r = app.test_client().post("/api/ingest/sessions", json={"started_at": "2026-11-01T10:00:00Z"}, headers=dev)
    assert r.status_code == 403
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_ingest_session_and_csv_export(app):
    consent(app, "P-001")
    dev = device_token(app, "P-001")
    samples = [
        {"t_ms": 0, "fsr_heel": 0.9, "emg": 0.1, "gait_phase": "stance", "phase_confidence": 0.95},
        {"t_ms": 10, "fsr_heel": 0.8, "emg": None, "gait_phase": "stance", "phase_confidence": 0.94},
        {"t_ms": 20, "fsr_heel": 0.7, "emg": 0.2, "gait_phase": None, "valid": 0},
    ]
    c = app.test_client()
    r = c.post("/api/ingest/sessions", headers=dev, json={
        "started_at": "2026-11-01T10:00:00Z", "strides": 100, "stride_time_cv_pct": 3.1, "samples": samples})
    assert r.status_code == 201, r.data
    sid = r.get_json()["session_id"]

    r = c.post(f"/api/ingest/sessions/{sid}/samples", headers=dev, json={"samples": [{"t_ms": 30, "fsr_heel": 0.1}]})
    assert r.get_json()["samples_stored"] == 1

    doc = login(app, "doc", "pw")
    rows = list(csv.DictReader(io.StringIO(doc.get(f"/api/sessions/{sid}/export.csv").data.decode())))
    assert [r["t_ms"] for r in rows] == ["0", "10", "20", "30"]
    assert all(r["participant_id"] == "P-001" for r in rows)
    assert rows[1]["emg"] == ""          # missing reading stays identifiable (NFR-11)
    assert rows[2]["valid"] == "0"       # invalid frame flagged

    # The other participant can't see it.
    assert login(app, "P-002", "p2").get(f"/api/sessions/{sid}").status_code == 404


def test_device_cannot_write_to_another_participants_session(app):
    consent(app, "P-001")
    consent(app, "P-002")
    d1, d2 = device_token(app, "P-001"), device_token(app, "P-002")
    c = app.test_client()
    sid = c.post("/api/ingest/sessions", headers=d1, json={"started_at": "2026-11-01T10:00:00Z"}).get_json()["session_id"]
    assert c.post(f"/api/ingest/sessions/{sid}/samples", headers=d2, json={"samples": []}).status_code == 404
    r = c.post("/api/ingest/events", headers=d2, json={"events": [
        {"category": "anomaly", "type": "toe_walking", "session_id": sid}]})
    assert r.status_code == 422


def test_ingest_events(app):
    consent(app, "P-001")
    dev = device_token(app, "P-001")
    c = app.test_client()
    r = c.post("/api/ingest/events", headers=dev, json={"events": [
        {"category": "anomaly", "type": "toe_dragging", "detail": {"bout_strides": 12}},
        {"category": "safety", "type": "plantar_overload"}]})
    assert r.status_code == 201
    assert c.post("/api/ingest/events", headers=dev, json={"events": [{"category": "anomaly", "type": "dancing"}]}).status_code == 422
    assert c.post("/api/ingest/events", headers=dev, json={"events": [
        {"category": "config", "type": "support_level_changed"}]}).status_code == 422

    doc = login(app, "doc", "pw")
    open_ = doc.get("/api/participants/P-001/events?open=1").get_json()
    assert {e["type"] for e in open_} == {"toe_dragging", "plantar_overload"}
    crit = next(e for e in open_ if e["type"] == "plantar_overload")
    assert crit["severity"] == "critical"
    assert doc.post(f"/api/events/{crit['id']}/acknowledge", headers=H).get_json()["acknowledged_by"] == "doc"
    assert len(doc.get("/api/participants/P-001/events?open=1").get_json()) == 1


def test_device_config_endpoint(app):
    dev = device_token(app, "P-002")
    cfg = app.test_client().get("/api/ingest/config", headers=dev).get_json()
    assert cfg["participant_id"] == "P-002"
    assert cfg["torque_capped"] is True
    assert cfg["full_support_torque_nm"] == 50


def test_participant_consent_and_delete(app):
    dev = device_token(app, "P-001")
    c = login(app, "P-001", "p1")
    assert c.post("/api/participants/P-001/consent", json={"agree": False}, headers=H).status_code == 422
    assert c.post("/api/participants/P-001/consent", json={"agree": True}, headers=H).get_json()["consented"]
    app.test_client().post("/api/ingest/sessions", headers=dev, json={
        "started_at": "2026-11-01T10:00:00Z", "samples": [{"t_ms": 0}]})
    app.test_client().post("/api/ingest/events", headers=dev, json={"events": [{"category": "anomaly", "type": "toe_walking"}]})

    r = c.delete("/api/participants/P-001/data", headers=H)
    assert r.get_json() == {"sessions_deleted": 1, "events_deleted": 2}  # anomaly + consent_given
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
        assert db.execute("SELECT consent_given_at FROM participants WHERE id='P-001'").fetchone()[0] is None
    # Consent withdrawn: the brace can't upload again.
    assert app.test_client().post("/api/ingest/sessions", headers=dev,
                                  json={"started_at": "2026-11-02T10:00:00Z"}).status_code == 403


def test_other_participant_cannot_consent_or_delete(app):
    c = login(app, "P-002", "p2")
    assert c.post("/api/participants/P-001/consent", json={"agree": True}, headers=H).status_code == 404
    assert c.delete("/api/participants/P-001/data", headers=H).status_code == 404


def test_consent_page_gates_patient_view(app):
    c = login(app, "P-001", "p1")
    assert c.get("/me").headers["Location"].endswith("/consent")
    c.post("/consent", data={"agree": "yes"})
    assert c.get("/me").status_code == 200


# ------------------------------------------------------------------ retention

def test_purge_expired(app):
    consent(app, "P-001")
    with app.app_context():
        db = get_db()
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        new = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        for ts in (old, new):
            db.execute("INSERT INTO sessions (participant_id, started_at) VALUES ('P-001', ?)", (ts,))
        db.commit()
        out = service.purge_expired()
        assert out["sessions_deleted"] == 1
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_estop_is_logged(app):
    c = login(app, "P-001", "p1")
    r = c.post("/api/participants/P-001/estop", json={"ble_delivered": False, "ble_error": "no bluetooth"}, headers=H)
    assert r.status_code == 201
    ev = c.get("/api/participants/P-001/events?category=safety").get_json()[0]
    assert ev["type"] == "app_estop" and ev["severity"] == "critical"


def test_demo_seed(app, tmp_path):
    fresh = create_app({"TESTING": True, "DATABASE": str(tmp_path / "demo.db"), "SECRET_KEY": "t"})
    with fresh.app_context():
        from smartsplint_web.seed import seed_demo
        seed_demo()
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM sessions WHERE participant_id='P-005'").fetchone()[0] == 0
