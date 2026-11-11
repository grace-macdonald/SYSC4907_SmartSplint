"""Synthetic demo data so the dashboards can be explored before the brace streams real data.

Everything here is invented. The waveforms are rough shapes for a normal
walking cycle, not physiological models, and must never be mixed with real
participant data.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone

from flask import current_app

from . import auth, domain, service
from .db import get_db

CLINICIAN = ("demo-clinician", "demo-clinician")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# Each profile describes how a participant's metrics move from week 0 to their last week.
PROFILES = [
    dict(pid="P-001", mass=71.0, weeks=6, level=(60, 25), wbl=(40, 70), anomalies=(6, 1),
         cv=(6.5, 2.4), cadence=(78, 104), emg=(0.35, 0.78), rom=(14, 27), load=(32, 66),
         conf=(0.84, 0.95), safety=["low_battery"], password="demo-p001"),
    dict(pid="P-002", mass=95.0, weeks=4, level=(50, 35), wbl=(None, None), anomalies=(4, 2),
         cv=(5.0, 3.4), cadence=(84, 98), emg=(0.40, 0.62), rom=(16, 22), load=(45, 80),
         conf=(0.86, 0.93), safety=["comm_loss"], password="demo-p002"),
    dict(pid="P-003", mass=64.0, weeks=5, level=(45, 45), wbl=(35, 35), anomalies=(5, 6),
         cv=(6.0, 6.6), cadence=(80, 82), emg=(0.38, 0.42), rom=(15, 16), load=(30, 37),
         conf=(0.83, 0.81), safety=["plantar_overload", "plantar_overload", "sensor_dropout", "hardware_estop"],
         password="demo-p003"),
]


def _gait_samples(rng: random.Random, seconds: float, hz: int, ref_torque: float, cadence: float,
                  quality: float) -> tuple[list[dict], list[float]]:
    """Synthetic synchronised samples. Returns (samples, stride start times in s)."""
    samples = []
    stride_starts = []
    t = 0.0
    base_period = 120.0 / cadence  # stride = 2 steps
    dt = 1.0 / hz
    while t < seconds:
        period = base_period * (1 + rng.gauss(0, 0.06 * (1.2 - quality)))
        stride_starts.append(t)
        n = int(period * hz)
        weak = rng.random() > quality + 0.15
        for i in range(n):
            ts = t + i * dt
            if ts >= seconds:
                break
            c = i / n  # 0..1 through the stride, heel strike at 0
            if c < 0.45:
                phase = "stance"
            elif c < 0.62:
                phase = "push_off"
            else:
                phase = "swing"
            heel = max(0.0, math.sin(math.pi * min(c / 0.35, 1))) if c < 0.35 else 0.0
            lateral = max(0.0, math.sin(math.pi * (c - 0.08) / 0.42)) * 0.6 if 0.08 < c < 0.5 else 0.0
            medial = max(0.0, math.sin(math.pi * (c - 0.2) / 0.4)) * 0.8 if 0.2 < c < 0.6 else 0.0
            toe = max(0.0, math.sin(math.pi * (c - 0.35) / 0.27)) if 0.35 < c < 0.62 else 0.0
            if c < 0.45:
                angle = _lerp(-3, 10, c / 0.45)
            elif c < 0.62:
                angle = _lerp(10, -16 * (0.6 if weak else 1), (c - 0.45) / 0.17)
            else:
                angle = _lerp(-16 * (0.6 if weak else 1), -3, (c - 0.62) / 0.38)
            if phase == "push_off":
                torque = ref_torque * math.sin(math.pi * (c - 0.45) / 0.17)
            elif phase == "swing":
                torque = 2.0  # small fixed dorsiflexion assist (P8)
            else:
                torque = 0.0
            emg = (0.15 + 0.85 * math.sin(math.pi * (c - 0.15) / 0.47)) if 0.15 < c < 0.62 else 0.08
            emg *= 0.7 + 0.3 * quality
            conf = min(1.0, max(0.0, rng.gauss(0.6 + 0.35 * quality, 0.05)))
            s = {
                "t_ms": int(round(ts * 1000)),
                "fsr_heel": round(heel + rng.gauss(0, 0.02), 3),
                "fsr_medial": round(medial + rng.gauss(0, 0.02), 3),
                "fsr_lateral": round(lateral + rng.gauss(0, 0.02), 3),
                "fsr_toe": round(toe + rng.gauss(0, 0.02), 3),
                "emg": round(max(0.0, emg + rng.gauss(0, 0.04)), 3),
                "imu_foot_pitch": round(angle * 1.4 + rng.gauss(0, 0.5), 2),
                "imu_foot_gyro": round(math.cos(2 * math.pi * c) * 180 + rng.gauss(0, 8), 1),
                "imu_shank_pitch": round(_lerp(-20, 25, c) if c < 0.6 else _lerp(25, -20, (c - 0.6) / 0.4), 2),
                "imu_shank_gyro": round(math.sin(2 * math.pi * c) * 120 + rng.gauss(0, 6), 1),
                "ankle_angle_deg": round(angle + rng.gauss(0, 0.4), 2),
                "hinge_torque_nm": round(torque + rng.gauss(0, 0.3), 2),
                "gait_phase": phase,
                "phase_confidence": round(conf, 3),
                "valid": 1,
            }
            samples.append(s)
        t += period
    # A short EMG dropout and a couple of invalid frames, so missing data shows up (NFR-11).
    if len(samples) > 400:
        k = rng.randrange(100, len(samples) - 60)
        for s in samples[k:k + 25]:
            s["emg"] = None
        for s in rng.sample(samples, 3):
            s["valid"] = 0
    return samples, stride_starts


def seed_demo(now: datetime | None = None) -> str:
    db = get_db()
    if db.execute("SELECT 1 FROM participants LIMIT 1").fetchone():
        return "Database already has participants; run with --reset to replace it with demo data."
    rng = random.Random(4907)
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    safe = current_app.config["MOTOR_SAFE_TORQUE_NM"]

    auth.create_user(CLINICIAN[0], CLINICIAN[1], "clinician")

    for prof in PROFILES:
        pid = prof["pid"]
        service.create_participant(body_mass_kg=prof["mass"], pid=pid, password=prof["password"])
        start = (now - timedelta(weeks=prof["weeks"])).replace(hour=14, minute=0, second=0)
        db.execute("UPDATE participants SET created_at = ?, consent_given_at = ?, consent_version = ? WHERE id = ?",
                   (_iso(start - timedelta(days=1)), _iso(start - timedelta(days=1)), domain.CONSENT_VERSION, pid))

        # Calibration walk on day 0 (FR-4).
        cal_end = start + timedelta(minutes=5)
        db.execute("INSERT INTO sessions (participant_id, kind, started_at, ended_at, support_level_pct, "
                   "reference_torque_nm, strides, model_confidence_mean, notes) VALUES (?,?,?,?,?,?,?,?,?)",
                   (pid, "calibration", _iso(start), _iso(cal_end), 0, 0.0, 240, None,
                    "5-minute calibration walk for gait-model fine-tuning"))

        day = 1
        total_days = prof["weeks"] * 7
        prev_level = None
        session_ids = []
        while day < total_days:
            frac = day / total_days
            level = int(round(_lerp(*prof["level"], frac) / 5) * 5)
            if level != prev_level:
                ts = start + timedelta(days=day, hours=-2)
                service.record_event(pid, "config", "support_level_changed",
                                     {"from": prev_level or 0, "to": level, "by": CLINICIAN[0]},
                                     occurred_at=_iso(ts), commit=False)
                prev_level = level
            quality = _lerp(0.55, 0.95, frac) if prof["anomalies"][1] < prof["anomalies"][0] else 0.55
            ref = domain.reference_torque_nm(level, prof["mass"], safe)
            s_start = start + timedelta(days=day, minutes=rng.randint(-90, 240))
            minutes = rng.randint(12, 26)
            cadence = _lerp(*prof["cadence"], frac) + rng.gauss(0, 2)
            strides = int(minutes * cadence / 2)
            wbl = prof["wbl"][0] if frac < 0.5 else prof["wbl"][1]
            load = _lerp(*prof["load"], frac) + rng.gauss(0, 2)
            cur = db.execute(
                "INSERT INTO sessions (participant_id, kind, started_at, ended_at, support_level_pct, reference_torque_nm, "
                "strides, cadence_spm, stride_time_mean_s, stride_time_cv_pct, peak_plantar_load_kg, push_off_torque_nm, "
                "emg_peak_norm, rom_deg, assist_active_pct, model_confidence_mean) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, "walk", _iso(s_start), _iso(s_start + timedelta(minutes=minutes)), level, ref, strides,
                 round(cadence, 1), round(120 / cadence, 3),
                 round(max(1.0, _lerp(*prof["cv"], frac) + rng.gauss(0, 0.5)), 2),
                 round(load if wbl is None else min(load, wbl + 6), 1),
                 round(ref * (1 + rng.gauss(0, 0.04)), 2),
                 round(min(1.0, _lerp(*prof["emg"], frac) + rng.gauss(0, 0.04)), 3),
                 round(_lerp(*prof["rom"], frac) + rng.gauss(0, 1), 1),
                 round(min(100, _lerp(*prof["conf"], frac) * 100 + rng.gauss(0, 2)), 1),
                 round(_lerp(*prof["conf"], frac) + rng.gauss(0, 0.01), 3)))
            sid = cur.lastrowid
            session_ids.append((sid, s_start, minutes, level, ref, cadence, quality, frac))

            n_anom = max(0, int(round(_lerp(*prof["anomalies"], frac) + rng.gauss(0, 1))))
            for _ in range(n_anom):
                kind = rng.choices(["toe_walking", "toe_dragging", "stride_variation", "weak_push_off"],
                                   weights=[3, 3, 2, 1])[0]
                at = s_start + timedelta(seconds=rng.randint(30, minutes * 60 - 10))
                detail = {"bout_strides": rng.randint(10, 24)}
                if kind == "stride_variation":
                    detail["stride_time_cv_pct"] = round(rng.uniform(7, 11), 1)
                eid = service.record_event(pid, "anomaly", kind, detail, session_id=sid,
                                           occurred_at=_iso(at), commit=False)
                if at < now - timedelta(days=4):
                    db.execute("UPDATE events SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
                               (_iso(at + timedelta(hours=20)), CLINICIAN[0], eid))
            day += rng.choice([1, 1, 2])

        # Safety events spread over the last part of the programme; the latest stay open.
        for i, kind in enumerate(prof["safety"]):
            sid, s_start, minutes, *_ = session_ids[-(1 + 3 * i) if len(session_ids) > 3 * i else -1]
            at = s_start + timedelta(minutes=rng.randint(2, minutes - 1))
            detail = {"plantar_load_kg": (prof["wbl"][1] or 0) + 9.5, "limit_kg": prof["wbl"][1]} \
                if kind == "plantar_overload" else {"battery_pct": 9} if kind == "low_battery" else None
            eid = service.record_event(pid, "safety", kind, detail, session_id=sid, occurred_at=_iso(at), commit=False)
            if i > 0 and kind != "plantar_overload":
                db.execute("UPDATE events SET acknowledged_at = ?, acknowledged_by = ? WHERE id = ?",
                           (_iso(at + timedelta(hours=3)), CLINICIAN[0], eid))

        db.execute("UPDATE participants SET support_level_pct = ?, support_max_pct = ?, weight_bearing_limit_kg = ?, "
                   "rom_dorsi_limit_deg = ? WHERE id = ?",
                   (prev_level, max(prof["level"]), prof["wbl"][1], 10 if pid == "P-003" else 15, pid))

        # Raw synchronised samples for the two most recent sessions (60 s at 100 Hz each).
        for sid, s_start, minutes, level, ref, cadence, quality, frac in session_ids[-2:]:
            samples, _ = _gait_samples(rng, 60, 100, ref or 0.0, cadence, quality)
            service.insert_samples(sid, samples)

    # A participant who has just been enrolled: consented, calibration walk only.
    service.create_participant(body_mass_kg=78.0, pid="P-004", password="demo-p004")
    t = now - timedelta(days=1)
    db.execute("UPDATE participants SET consent_given_at = ?, consent_version = ?, support_max_pct = 30 WHERE id = ?",
               (_iso(t - timedelta(hours=1)), domain.CONSENT_VERSION, "P-004"))
    db.execute("INSERT INTO sessions (participant_id, kind, started_at, ended_at, support_level_pct, reference_torque_nm,"
               " strides, notes) VALUES (?,?,?,?,?,?,?,?)",
               ("P-004", "calibration", _iso(t), _iso(t + timedelta(minutes=5)), 0, 0.0, 236,
                "5-minute calibration walk for gait-model fine-tuning"))

    # Enrolled but has not consented yet: nothing can be stored.
    service.create_participant(body_mass_kg=None, pid="P-005", password="demo-p005")
    db.commit()

    return json.dumps({
        "clinician_login": {"username": CLINICIAN[0], "password": CLINICIAN[1]},
        "participant_logins": {p["pid"]: p["password"] for p in PROFILES} | {"P-004": "demo-p004",
                                                                             "P-005": "demo-p005"},
    }, indent=2)
