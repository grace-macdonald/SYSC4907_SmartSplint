# Smart Splint web app

This web app lets participants and clinicians follow recovery with the Smart Splint brace. It implements the "Web app" block in the proposal's system diagrams (Figures 1.1 and 1.2).

- **Participants** sign in with their participant ID. They see their support level, weekly totals, alerts written in plain language, progress charts and their own sessions. They can also download or delete their data. A gait alert reaches an open page within a few seconds (P9: 5 s target), as a toast and optionally as a system notification.
- **Clinicians and researchers** see all participants, sorted so that open safety events come first. For each participant they can set body mass, support level, the maximum level and post-op limits, and review progress. They can acknowledge alerts, read the event log, and open any session to see every synchronised signal with the detected gait phase.
- **The brace**, or the phone relaying for it, uploads sessions, raw samples and events to a token-authenticated ingest API, and reads its settings back from it.

Built with Flask and SQLite. The front end is plain HTML and ES modules with hand-written SVG charts, so it has no JavaScript build step or npm dependencies.

## Quick start

```bash
cd webapp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export SMARTSPLINT_SECRET_KEY=dev-only-change-me
flask --app smartsplint_web init-demo        # synthetic demo data (prints the logins)
flask --app smartsplint_web run --debug      # http://127.0.0.1:5000
```

Demo logins: `demo-clinician` / `demo-clinician` for the clinician view. Participants P-001 to P-005 use `demo-p001` to `demo-p005`.

- P-001 is recovering well.
- P-002's 100% torque is capped (95 kg).
- P-003 has plateaued and has open safety events.
- P-004 was just enrolled.
- P-005 hasn't given consent yet.

The demo data is synthetic. Use `init-demo --reset` to start over, or skip `init-demo` and create real accounts:

```bash
flask --app smartsplint_web create-clinician <username>
flask --app smartsplint_web create-participant --mass 72.5   # prints the ID and a temporary password
flask --app smartsplint_web create-device P-006              # prints the brace's ingest token (shown once)
```

Run the tests with `python -m pytest tests`.

## How the proposal maps to the app

| Proposal | Where it lives |
|---|---|
| FR-2: common clock, participant ID, CSV export | `samples` table keyed by `(session_id, t_ms)`. **Download synchronised log (CSV)** on every session. |
| FR-6 / P8: 100% = mass × 0.61 N·m/kg, capped at the safe motor torque, levels 0–100% in 5% steps | `domain.py`. Validated in `service.update_settings`. Shown live in the clinician settings form. |
| Section 1.11: clinician-set weight-bearing and ROM limits that can only tighten the hard stops (NFR-3) | Participant settings. ROM values above 25° plantarflexion or 15° dorsiflexion are rejected. |
| Table 3: the app only changes support within clinician limits and never starts motion | Only clinicians can change settings. The level can't exceed the participant's maximum. Every change is logged with who made it. |
| P9 / FR-9: toe walking, toe dragging and stride variation, with notification within 5 s | `POST /api/ingest/events`. Pages poll every 3 s and show a toast or system notification. |
| FR-11: safe-state entries (watchdog, sensor dropout, stale prediction, low battery, comm loss, over-temperature, plantar overload) | Event type `safety`. Shown as critical and listed first for clinicians. |
| P10 / section 1.7: e-stop in the app | **Stop brace** button: Web Bluetooth write, then logged as a critical event. |
| FR-12 / P12: consent before storage, deletion on request | Participants must consent before the ingest API accepts anything (it returns 403 otherwise). **Delete all my data** removes everything and withdraws consent. |
| Section 1.12: participant IDs only, 12-month retention, access control | No name columns exist (a test checks this). `purge-expired` runs at start-up and from the CLI. Participants can only reach their own records. |
| NFR-11: missing or invalid readings are identifiable | A missing reading is `NULL` (empty in the CSV) and `valid = 0` marks a bad frame. Both draw as gaps in the charts. |
| S3 / S4 (stretch): visualise gait, clinician view | Session viewer with a gait-phase strip, plus the whole clinician interface. |

## Device ingest API

Every request sends `Authorization: Bearer <token from create-device>`. A token is bound to one participant, so a brace can only write to that participant's record. All endpoints return 403 until the participant has consented.

```http
GET  /api/ingest/config
     -> { participant_id, consented, body_mass_kg, support_level_pct, full_support_torque_nm,
          reference_torque_nm, torque_capped, weight_bearing_limit_kg, rom_plantar_limit_deg, rom_dorsi_limit_deg }

POST /api/ingest/sessions
     { "kind": "walk" | "calibration" | "bench",
       "started_at": "2026-11-10T14:00:00Z", "ended_at": "...",          # ISO-8601 UTC
       "support_level_pct": 25,                                          # defaults to the current setting
       "strides": 620, "cadence_spm": 103, "stride_time_mean_s": 1.16, "stride_time_cv_pct": 2.2,
       "peak_plantar_load_kg": 63.4, "push_off_torque_nm": 11.6, "emg_peak_norm": 0.74,
       "rom_deg": 26, "assist_active_pct": 90, "model_confidence_mean": 0.94,
       "samples": [ ... optional, see below ... ] }
     -> 201 { "session_id": 42, "samples_stored": 0 }

PATCH /api/ingest/sessions/<id>          # fill in ended_at / summary metrics later
POST  /api/ingest/sessions/<id>/samples  { "samples": [ ... ] }   # append in chunks

# one sample (all sensor fields optional; null = missing reading)
{ "t_ms": 1234, "fsr_heel": 0.8, "fsr_medial": 0.1, "fsr_lateral": 0.3, "fsr_toe": 0.0, "emg": 0.42,
  "imu_foot_pitch": 4.1, "imu_foot_gyro": 120, "imu_shank_pitch": -3.0, "imu_shank_gyro": 80,
  "ankle_angle_deg": 6.2, "hinge_torque_nm": 0.0, "gait_phase": "stance" | "push_off" | "swing" | null,
  "phase_confidence": 0.93, "valid": 1 }

POST /api/ingest/events
     { "events": [ { "category": "anomaly", "type": "toe_dragging", "session_id": 42,
                     "occurred_at": "2026-11-10T14:03:12.500Z", "detail": { "bout_strides": 12 } } ] }
```

The event types are listed in `domain.EVENT_TYPES`:

- **Anomaly:** `toe_walking`, `toe_dragging`, `stride_variation`, `weak_push_off`.
- **Safety:** `hardware_estop`, `app_estop`, `watchdog_timeout`, `sensor_dropout`, `stale_prediction`, `low_battery`, `comm_loss`, `over_temperature`, `plantar_overload`, `torque_limit`.

Severity comes from that catalogue.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `SMARTSPLINT_SECRET_KEY` | random per start | **Required** outside development. Without it, logins reset on restart. |
| `SMARTSPLINT_DATABASE` | `instance/smartsplint.db` | SQLite path. |
| `SMARTSPLINT_MOTOR_SAFE_TORQUE_NM` | `50.0` | **Placeholder.** Replace it with the value the P8 bench test verifies. |
| `SMARTSPLINT_MOTOR_SAFE_TORQUE_VERIFIED` | unset | Set to `1` once the value is verified. This hides the warning in the clinician view. |
| `SMARTSPLINT_BLE_SERVICE_UUID`, `SMARTSPLINT_BLE_ESTOP_CHAR_UUID` | unset | Must match the firmware's GATT service and characteristic (see below). |
| `SMARTSPLINT_SECURE_COOKIES` | unset | Set to `1` whenever the app is served over HTTPS. |

## App e-stop and the firmware

The **Stop brace** button uses Web Bluetooth. It filters for `SMARTSPLINT_BLE_SERVICE_UUID`, connects, and writes a single byte `0x01` to `SMARTSPLINT_BLE_ESTOP_CHAR_UUID` with response. The firmware's safety limiter should treat that write as "set motor command to zero and latch". Until the UUIDs are configured, the button tells the user to press the hardware stop and logs the press as not delivered.

The proposal treats this stop as a convenience; the latching hardware button is the real stop, and the UI says so.

Web Bluetooth only works in Chrome or Edge (desktop and Android), over HTTPS or localhost. iOS Safari doesn't support it.

## Before real participant data

The app is ready for team testing. Before it holds data from anyone outside the team:

- Serve it over HTTPS and set `SMARTSPLINT_SECURE_COOKIES=1` and a real `SMARTSPLINT_SECRET_KEY`.
- Put the database on encrypted storage, or move to the team's managed cloud database (section 1.12). The SQL in `service.py` is plain and ports easily to PostgreSQL.
- Schedule `flask --app smartsplint_web purge-expired` to run daily.
- Restrict who can run the CLI, since that is how clinician accounts and device tokens are issued.

## Open points to settle with the team

- **Motor safe torque.** It is still a placeholder (see Configuration).
- **BLE UUIDs and e-stop command format.** These need to be agreed with the firmware (`rtos_environment` branch).
- **Plantar load in kg.** The proposal says the FSRs won't be calibrated, but the weight-bearing limit needs a load estimate. The app stores whatever kg estimate the pipeline sends.
- **Where summary metrics are computed.** They could be computed on the brace, on the phone or in the cloud anomaly checker. The ingest API accepts them from any of these.
- **Long recordings.** Charts load up to 20,000 samples per session (about 3 minutes at 100 Hz) and downsample longer ones. The CSV export always contains every sample.
- **Per-stride gait map (S3).** The session viewer shows the phase over time. A per-stride pressure map is not built yet.
