"""Server-side toe-walking check on raw insole samples.

A direct port of `HeelCheck` in FootAnomaly/plot_fsr.py (dev-footanomaly), with
the same constants (domain.CONTACT_ON / CONTACT_OFF / TOE_WALK_WINDOW_MS), so the
web app flags exactly the windows the team's live plot and the board flag:

  * Each FSR has hysteresis contact: on above CONTACT_ON, off below CONTACT_OFF.
  * Every sample records whether any non-heel sensor and/or the heel is in contact.
  * When a sample arrives TOE_WALK_WINDOW_MS or more after the window started, the
    window closes:
        nothing in contact all window   -> "no contact"   (sitting, foot in the air)
        foot loaded, heel never         -> "toe walking"
        heel touched down at least once -> "normal"
    and the next window starts at that sample.

Differences from the Python plotter, all needed for stored data:
  * Times are in ms from the session's first sample (the plotter uses seconds).
  * A missing reading (None) leaves that sensor's contact state unchanged.
  * Invalid frames (valid = 0) don't change contact state but still advance time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import domain

NORMAL = "normal"
TOE_WALKING = "toe walking"
NO_CONTACT = "no contact"


@dataclass
class HeelCheck:
    sensors: list[str] = field(default_factory=lambda: list(domain.FSR_KEYS))
    heel: str = domain.HEEL_SENSOR
    on: dict[str, bool] = field(default_factory=dict)
    start: int | None = None
    any_seen: bool = False
    heel_seen: bool = False

    def update(self, t_ms: int, sample: dict) -> tuple[int, int, str] | None:
        """Feed one sample. Returns (start_ms, end_ms, verdict) when a window closes."""
        if sample.get("valid", 1):
            for k in self.sensors:
                v = sample.get(k)
                if v is None:
                    continue
                if not self.on.get(k) and v > domain.CONTACT_ON:
                    self.on[k] = True
                elif self.on.get(k) and v < domain.CONTACT_OFF:
                    self.on[k] = False
        if self.start is None:
            self.start = t_ms

        other = any(self.on.get(k) for k in self.sensors if k != self.heel)
        heel = bool(self.on.get(self.heel))
        if heel:
            self.any_seen = self.heel_seen = True
        elif other:
            self.any_seen = True

        if t_ms - self.start >= domain.TOE_WALK_WINDOW_MS:
            verdict = NO_CONTACT if not self.any_seen else NORMAL if self.heel_seen else TOE_WALKING
            result = (self.start, t_ms, verdict)
            self.start, self.any_seen, self.heel_seen = t_ms, False, False
            return result
        return None


def check_samples(samples: list[dict]) -> list[tuple[int, int, str]]:
    """Run the check over samples ordered by t_ms. Returns every closed window,
    with times relative to the first sample."""
    if not samples:
        return []
    t0 = samples[0]["t_ms"]
    check = HeelCheck()
    windows = []
    for s in samples:
        r = check.update(s["t_ms"] - t0, s)
        if r:
            windows.append(r)
    return windows


def summarise(windows: list[tuple[int, int, str]]) -> dict:
    return {
        "window_s": domain.TOE_WALK_WINDOW_MS / 1000,
        "contact_on": domain.CONTACT_ON,
        "contact_off": domain.CONTACT_OFF,
        "heel_sensor": domain.HEEL_SENSOR,
        "windows": len(windows),
        "toe_walking": sum(1 for w in windows if w[2] == TOE_WALKING),
        "no_contact": sum(1 for w in windows if w[2] == NO_CONTACT),
        "flagged": [{"start_ms": a, "end_ms": b} for a, b, v in windows if v == TOE_WALKING],
    }
