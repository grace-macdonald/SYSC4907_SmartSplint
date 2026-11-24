"""Import raw insole recordings made with FootAnomaly/plot_fsr.py.

`python plot_fsr.py live --participant P01 --trial walk1` saves
data/P01_walk1_raw.csv with columns  pc_time_s, t_us, fsr1..fsr5  (raw ADC).
This turns one of those files into a session, then runs the same toe-walking
check the plotter runs, so the trial and its flags appear in the web app.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone

from flask import current_app

from . import domain, service
from .db import get_db


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_plot_fsr_csv(path: str) -> list[dict]:
    """Parse a plot_fsr.py raw CSV into samples with t_ms from the first row."""
    with open(path, newline="") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    cols = reader.fieldnames or []
    present = [k for k in domain.FSR_KEYS if k in cols]
    if "t_us" not in cols or not present:
        raise service.ValidationError(
            f"{os.path.basename(path)} doesn't look like a plot_fsr.py recording "
            f"(needs t_us and {', '.join(domain.FSR_KEYS)} columns).")
    samples: list[dict] = []
    prev_us = None
    elapsed_us = 0
    seen_ms = set()
    for i, row in enumerate(reader, start=2):
        try:
            us = int(row["t_us"])
            vals = {k: int(row[k]) for k in present}
        except (TypeError, ValueError):
            continue  # same as plot_fsr.py: skip malformed lines
        if prev_us is not None:
            elapsed_us += (us - prev_us) & 0xFFFFFFFF  # survives micros() wrap, as in replay()
        prev_us = us
        t_ms = round(elapsed_us / 1000)
        if t_ms in seen_ms:
            continue
        seen_ms.add(t_ms)
        for k, v in vals.items():
            if not 0 <= v <= domain.FSR_ADC_MAX:
                raise service.ValidationError(f"Line {i}: {k}={v} is outside 0-{domain.FSR_ADC_MAX}.")
        samples.append({"t_ms": t_ms, **vals, "valid": 1})
    if not samples:
        raise service.ValidationError("No sample rows found.")
    return samples


def import_plot_fsr_csv(pid: str, path: str, started_at: datetime | None = None,
                        kind: str = "walk", detect: bool = True) -> dict:
    p = service.get_participant(pid)
    if p is None:
        raise service.ValidationError(f"No participant {pid}.")
    if p["consent_given_at"] is None:
        raise service.ValidationError(f"{pid} hasn't given consent, so no data can be stored (FR-12).")
    samples = read_plot_fsr_csv(path)
    duration = timedelta(milliseconds=samples[-1]["t_ms"])
    if started_at is None:
        # plot_fsr.py closes the file when recording stops, so mtime ~ end of the trial.
        started_at = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc) - duration
    level = p["support_level_pct"]
    ref = domain.reference_torque_nm(level, p["body_mass_kg"], current_app.config["MOTOR_SAFE_TORQUE_NM"])
    db = get_db()
    cur = db.execute(
        "INSERT INTO sessions (participant_id, kind, started_at, ended_at, support_level_pct, reference_torque_nm, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, kind, _iso(started_at), _iso(started_at + duration), level, ref,
         f"Imported from {os.path.basename(path)} (plot_fsr.py)"))
    sid = cur.lastrowid
    service.insert_samples(sid, samples)
    flagged = service.record_toe_walking(sid) if detect else 0
    db.commit()
    check = service.foot_check(sid)
    return {"session_id": sid, "samples": len(samples), "duration_s": round(duration.total_seconds(), 1),
            "windows": check["windows"], "toe_walking_windows": check["toe_walking"], "events_created": flagged}
