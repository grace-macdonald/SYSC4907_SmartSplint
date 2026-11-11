"""Rules from the project proposal that the web app enforces.

Keeping them in one module means the numbers in the UI, the API validation and
the tests all come from the same place. Section/requirement IDs refer to the
SYSC4907 Smart Splint proposal.
"""

from __future__ import annotations

# FR-6: 100% support torque = body mass x torque-per-kg constant, capped at the
# motor's verified safe torque. The proposal uses 0.61 N*m/kg (low end of the
# 0.61-0.85 range) so testing starts at the lower torque.
TORQUE_PER_KG_NM = 0.61

# Support levels run 0..100% in 5% steps (FR-6, P8).
SUPPORT_STEP_PCT = 5
SUPPORT_MIN_PCT = 0
SUPPORT_MAX_PCT = 100

# NFR-3 mechanical hard stops (defaults; surgeon-set limits may be tighter).
DEFAULT_ROM_PLANTAR_DEG = 25.0
DEFAULT_ROM_DORSI_DEG = 15.0

# Section 1.12: data is deleted automatically after 12 months.
RETENTION_DAYS = 365

CONSENT_VERSION = "2026-11"

# Event catalogue. category -> type -> (label, default severity, patient-facing text)
EVENT_TYPES: dict[str, dict[str, tuple[str, str, str]]] = {
    "anomaly": {  # P9 (+ S1 weak push-off)
        "toe_walking": ("Toe walking", "warning",
                        "You were walking on your toes. Try to land on your heel first."),
        "toe_dragging": ("Toe dragging", "warning",
                         "Your toes were catching the ground during swing. Try to lift your foot a little more."),
        "stride_variation": ("Stride-to-stride variation", "warning",
                             "Your step timing was uneven. Slow down and keep a steady rhythm."),
        "weak_push_off": ("Weak push-off", "info",
                          "Your push-off was weaker than usual."),
    },
    "safety": {  # FR-10, FR-11, P10: every one of these ends in the safe state
        "hardware_estop": ("Hardware e-stop pressed", "critical",
                           "The emergency stop on the brace was pressed. The motors are off."),
        "app_estop": ("App e-stop pressed", "critical",
                      "The emergency stop in the app was pressed. The motors are off."),
        "watchdog_timeout": ("Watchdog timeout", "critical",
                             "The brace stopped assisting because of a software fault."),
        "sensor_dropout": ("Sensor dropout", "critical",
                           "A sensor stopped responding, so the brace stopped assisting."),
        "stale_prediction": ("Stale gait prediction", "critical",
                             "The brace stopped assisting because the gait model fell behind."),
        "low_battery": ("Low battery", "warning",
                        "The battery is low, so the brace stopped assisting. Please charge it off your body."),
        "comm_loss": ("Communication loss", "warning",
                      "The brace lost its connection and went passive."),
        "over_temperature": ("Over-temperature", "critical",
                             "The brace got too warm and stopped assisting. Take it off and let it cool."),
        "plantar_overload": ("Plantar load above limit", "critical",
                             "You put more weight on your foot than your care team allows. Assistance paused."),
        "torque_limit": ("Torque limit reached", "critical",
                         "The brace reached its torque limit and stopped assisting."),
    },
    "config": {
        "support_level_changed": ("Support level changed", "info", "Your support level was changed."),
        "limits_changed": ("Limits changed", "info", "Your care team updated your limits."),
        "body_mass_changed": ("Body mass updated", "info", "Your body mass was updated."),
        "consent_given": ("Consent recorded", "info", "You gave consent for data to be stored."),
    },
}


def event_label(category: str, type_: str) -> str:
    return EVENT_TYPES.get(category, {}).get(type_, (type_.replace("_", " ").capitalize(),))[0]


def is_valid_support_level(pct: object) -> bool:
    return (
        isinstance(pct, int)
        and not isinstance(pct, bool)
        and SUPPORT_MIN_PCT <= pct <= SUPPORT_MAX_PCT
        and pct % SUPPORT_STEP_PCT == 0
    )


def full_support_torque_nm(body_mass_kg: float | None, motor_safe_torque_nm: float) -> float | None:
    """100% support torque for a wearer (FR-6), capped at the safe motor torque."""
    if body_mass_kg is None:
        return None
    return round(min(body_mass_kg * TORQUE_PER_KG_NM, motor_safe_torque_nm), 2)


def reference_torque_nm(support_pct: int, body_mass_kg: float | None,
                        motor_safe_torque_nm: float) -> float | None:
    """Peak push-off torque requested from the motors at a support level (P8)."""
    full = full_support_torque_nm(body_mass_kg, motor_safe_torque_nm)
    if full is None:
        return None
    return round(full * support_pct / 100, 2)


def is_torque_capped(body_mass_kg: float | None, motor_safe_torque_nm: float) -> bool:
    return body_mass_kg is not None and body_mass_kg * TORQUE_PER_KG_NM > motor_safe_torque_nm
