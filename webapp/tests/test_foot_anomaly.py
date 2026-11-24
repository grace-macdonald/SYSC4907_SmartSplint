"""Toe-walking check (port of HeelCheck in FootAnomaly/plot_fsr.py), its ingest hook,
the plot_fsr.py CSV importer, and the 4-region -> 5-FSR migration."""

from __future__ import annotations

import sqlite3

import pytest

from smartsplint_web import create_app, domain, service
from smartsplint_web.db import get_db
from smartsplint_web.foot_anomaly import NO_CONTACT, NORMAL, TOE_WALKING, check_samples

from test_app import app, device_token, login  # noqa: F401  (fixtures/helpers)

UP, DOWN = 2000, 100   # clearly loaded / clearly unloaded raw ADC


def stride(t0, toe_walk=False, hz=20, period_ms=1000):
    """One stride of insole samples: heel strike, forefoot, swing."""
    out = []
    for i in range(period_ms * hz // 1000):
        t = t0 + i * 1000 // hz
        c = i / (period_ms * hz / 1000)
        stance = c < 0.6
        out.append({
            "t_ms": t,
            "fsr1": UP if 0.35 < c < 0.6 else DOWN,
            "fsr2": UP if (0.2 < c < 0.6 or (toe_walk and stance)) else DOWN,
            "fsr3": UP if (0.2 < c < 0.6 or (toe_walk and stance)) else DOWN,
            "fsr4": UP if 0.1 < c < 0.5 else DOWN,
            "fsr5": UP if (c < 0.35 and not toe_walk) else DOWN,
        })
    return out


def walk(seconds, toe_from=None, toe_to=None):
    out = []
    for k in range(seconds):
        toe = toe_from is not None and toe_from <= k < toe_to
        out += stride(k * 1000, toe_walk=toe)
    return out


# ------------------------------------------------------------------ the rule itself

def test_constants_match_footanomaly_code():
    # Values from five_fsr_toe_walking.ino / plot_fsr.py on dev-footanomaly.
    assert (domain.CONTACT_ON, domain.CONTACT_OFF, domain.TOE_WALK_WINDOW_MS) == (480, 320, 5000)
    assert domain.FSR_ADC_MAX == 4095
    assert domain.FSR_KEYS == ["fsr1", "fsr2", "fsr3", "fsr4", "fsr5"]
    assert domain.HEEL_SENSOR == "fsr5"


def test_normal_walking_is_not_flagged():
    verdicts = [v for *_, v in check_samples(walk(21))]
    assert verdicts == [NORMAL] * 4


def test_toe_walking_window_is_flagged():
    # As in plot_fsr.py, the sample that closes a window still counts toward it, so the
    # toe walking has to last through the first sample at 10.000 s for 5-10 s to be flagged.
    assert [v for *_, v in check_samples(walk(21, toe_from=5, toe_to=10))][1] == NORMAL
    windows = check_samples(walk(21, toe_from=5, toe_to=11))
    assert [v for *_, v in windows] == [NORMAL, TOE_WALKING, NORMAL, NORMAL]
    assert windows[1][:2] == (5000, 10000)


def test_one_heel_strike_clears_the_window():
    # Toe walking for 4.5 s of a 5 s window, then one heel strike: normal (heel was seen).
    s = walk(4, toe_from=0, toe_to=4) + stride(4000) + stride(5000, toe_walk=True)
    assert check_samples(s)[0][2] == NORMAL


def test_no_contact_window():
    still = [{"t_ms": t, **{k: DOWN for k in domain.FSR_KEYS}} for t in range(0, 6000, 50)]
    assert check_samples(still)[0][2] == NO_CONTACT


def test_hysteresis_between_thresholds():
    # Heel rises to 500 (contact), then sits at 400: still in contact (between 320 and 480).
    s = [{"t_ms": 0, "fsr2": UP, "fsr5": 500}]
    s += [{"t_ms": t, "fsr2": UP, "fsr5": 400} for t in range(50, 5100, 50)]
    s += [{"t_ms": t, "fsr2": UP, "fsr5": 400} for t in range(5100, 10200, 50)]
    w = check_samples(s)
    assert w[0][2] == NORMAL and w[1][2] == NORMAL  # never dropped below 320
    # A heel that only ever reaches 400 never makes contact.
    s2 = [{"t_ms": t, "fsr2": UP, "fsr5": 400} for t in range(0, 5100, 50)]
    assert check_samples(s2)[0][2] == TOE_WALKING


def test_missing_and_invalid_samples_keep_contact_state():
    s = [{"t_ms": 0, "fsr2": UP, "fsr5": UP}]
    s += [{"t_ms": t, "fsr2": UP, "fsr5": None} for t in range(50, 2600, 50)]
    s += [{"t_ms": t, "fsr2": UP, "fsr5": 0, "valid": 0} for t in range(2600, 5100, 50)]
    assert check_samples(s)[0][2] == NORMAL


# ------------------------------------------------------------------ ingest hook

def _consent(app, pid="P-001"):
    with app.app_context():
        service.give_consent(pid)


def _toe_events(app, sid):
    with app.app_context():
        return get_db().execute("SELECT COUNT(*) FROM events WHERE session_id = ? AND type = 'toe_walking'",
                                (sid,)).fetchone()[0]


def test_ingest_flags_toe_walking_and_is_idempotent(app):
    _consent(app)
    dev = device_token(app, "P-001")
    c = app.test_client()
    samples = walk(21, toe_from=5, toe_to=16)
    first, rest = samples[:150], samples[150:]   # 7.5 s, then the remainder
    r = c.post("/api/ingest/sessions", headers=dev, json={"started_at": "2026-11-20T15:00:00Z", "samples": first})
    sid = r.get_json()["session_id"]
    assert r.get_json()["toe_walking_events"] == 0          # the 5-10 s window hasn't closed yet
    r = c.post(f"/api/ingest/sessions/{sid}/samples", headers=dev, json={"samples": rest})
    assert _toe_events(app, sid) == 2                        # 5-10 s and 10-15 s
    # Re-checking adds nothing new.
    with app.app_context():
        assert service.record_toe_walking(sid) == 0

    doc = login(app, "doc", "pw")
    detail = doc.get(f"/api/sessions/{sid}").get_json()
    assert detail["foot_check"]["toe_walking"] == 2
    assert [w["start_ms"] for w in detail["foot_check"]["flagged"]] == [5000, 10000]
    ev = next(e for e in detail["events"] if e["type"] == "toe_walking")
    assert ev["detail"]["source"] == "server" and ev["detail"]["heel_sensor"] == "fsr5"
    assert ev["occurred_at"] == "2026-11-20T15:00:10.000Z"


def test_ingest_detection_can_be_turned_off(app):
    _consent(app)
    dev = device_token(app, "P-001")
    r = app.test_client().post("/api/ingest/sessions?detect=0", headers=dev, json={
        "started_at": "2026-11-20T15:00:00Z", "samples": walk(12, toe_from=0, toe_to=12)})
    assert r.get_json()["toe_walking_events"] == 0
    assert _toe_events(app, r.get_json()["session_id"]) == 0


@pytest.mark.parametrize("bad", [4096, -1, 1.5, "100", True])
def test_fsr_values_must_be_raw_adc(app, bad):
    _consent(app)
    dev = device_token(app, "P-001")
    r = app.test_client().post("/api/ingest/sessions", headers=dev, json={
        "started_at": "2026-11-20T15:00:00Z", "samples": [{"t_ms": 0, "fsr3": bad}]})
    assert r.status_code == 422
    assert "raw ADC" in r.get_json()["error"]


# ------------------------------------------------------------------ plot_fsr.py importer

def _write_plot_fsr_csv(path, samples, start_us=4294000000):
    """Write the format plot_fsr.py live saves, starting near the micros() wrap."""
    lines = ["pc_time_s,t_us,fsr1,fsr2,fsr3,fsr4,fsr5", "# board status line, ignored"]
    for s in samples:
        us = (start_us + s["t_ms"] * 1000) & 0xFFFFFFFF
        lines.append(f"{s['t_ms'] / 1000:.4f},{us},{s['fsr1']},{s['fsr2']},{s['fsr3']},{s['fsr4']},{s['fsr5']}")
    lines.append("garbage,line")
    path.write_text("\n".join(lines) + "\n")


def test_import_plot_fsr_csv(app, tmp_path):
    from datetime import datetime, timezone
    from smartsplint_web.importer import import_plot_fsr_csv
    f = tmp_path / "P01_walk1_raw.csv"
    _write_plot_fsr_csv(f, walk(16, toe_from=10, toe_to=16))
    with app.app_context():
        with pytest.raises(service.ValidationError, match="consent"):
            import_plot_fsr_csv("P-001", str(f))
        service.give_consent("P-001")
        out = import_plot_fsr_csv("P-001", str(f), started_at=datetime(2026, 11, 20, 15, tzinfo=timezone.utc))
        assert out["samples"] == 320 and out["duration_s"] == pytest.approx(15.95, abs=0.06)
        assert out["toe_walking_windows"] == 1 and out["events_created"] == 1
        row = get_db().execute("SELECT * FROM sessions WHERE id = ?", (out["session_id"],)).fetchone()
        assert row["started_at"] == "2026-11-20T15:00:00.000Z"
        assert "plot_fsr.py" in row["notes"]


def test_import_cli(app, tmp_path):
    f = tmp_path / "trial.csv"
    _write_plot_fsr_csv(f, walk(6))
    with app.app_context():
        service.give_consent("P-001")
    result = app.test_cli_runner().invoke(args=["import-fsr-csv", "P-001", str(f), "--started-at", "2026-11-20T10:00:00Z"])
    assert result.exit_code == 0, result.output
    assert "'toe_walking_windows': 0" in result.output


def test_import_rejects_other_csvs(app, tmp_path):
    from smartsplint_web.importer import import_plot_fsr_csv
    f = tmp_path / "x.csv"
    f.write_text("a,b\n1,2\n")
    with app.app_context():
        service.give_consent("P-001")
        with pytest.raises(service.ValidationError, match="plot_fsr.py"):
            import_plot_fsr_csv("P-001", str(f))


# ------------------------------------------------------------------ migration

def test_old_four_region_database_is_migrated(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE samples (session_id INTEGER NOT NULL, t_ms INTEGER NOT NULL,
            fsr_heel REAL, fsr_medial REAL, fsr_lateral REAL, fsr_toe REAL, emg REAL,
            PRIMARY KEY (session_id, t_ms)) WITHOUT ROWID;
        INSERT INTO samples VALUES (1, 0, 0.9, 0.1, 0.2, 0.3, 0.5);
    """)
    conn.close()
    app = create_app({"TESTING": True, "DATABASE": str(db_path), "SECRET_KEY": "t"})
    with app.app_context():
        cols = [r[1] for r in get_db().execute("PRAGMA table_info(samples)").fetchall()]
        assert {"fsr1", "fsr2", "fsr3", "fsr4", "fsr5", "fsr6"} <= set(cols)
        assert not any(c.startswith("fsr_") for c in cols)
        row = get_db().execute("SELECT fsr1, fsr2, fsr4, fsr5 FROM samples").fetchone()
        assert tuple(row) == (0.3, 0.1, 0.2, 0.9)
