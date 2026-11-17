"""
plot_fsr.py -- live plot of the 5 FSRs streamed by five_fsr_toe_walking.ino,
with a HEEL WALKING flag printed to this terminal whenever the heel (FSR5)
never touches the ground during a window.

  1) Live from the board (close the Arduino Serial Monitor first, only one
     program can hold the port):
       python plot_fsr.py live --port COM6 --participant P01 --trial walk1
     Saves data/P01_walk1_raw.csv  (columns: pc_time_s, t_us, fsr1..fsr5)
     Close the plot window or press Ctrl+C to stop.

  2) Re-plot a saved trial (re-runs the same check offline):
       python plot_fsr.py replay data/P01_walk1_raw.csv
       python plot_fsr.py replay data/P01_walk1_raw.csv --save walk1.png

Sensors (red numbers on the foot map): 1 big toe, 2 1st metatarsal,
3 2nd/3rd metatarsal, 4 5th metatarsal, 5 heel.

The check runs here in Python on the incoming data, so it only depends on
FSR5. Status lines from the board ('#...') are still printed as [board].

Requires: pip install pyserial pandas numpy matplotlib
"""

import argparse
import collections
import csv
import os
import sys
import time

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

SENSORS = ["FSR1 big toe", "FSR2 1st metatarsal", "FSR3 2nd/3rd metatarsal",
           "FSR4 5th metatarsal", "FSR5 heel"]
COLS = ["fsr1", "fsr2", "fsr3", "fsr4", "fsr5"]
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
FLAG_COLOR = "#c0392b"
FLAG_LABEL = "TOE WALKING"     # text printed when FSR5 never touches down
HEEL = 4                        # index of FSR5 in COLS

# Keep these in sync with the .ino
CONTACT_ON, CONTACT_OFF = 480, 320
WINDOW_S = 5.0
ADC_MAX = 4095
SAMPLE_HZ = 200

RED, RESET = "\033[91m", "\033[0m"


def print_flag(a, b):
    print(f"{RED}*** {FLAG_LABEL} {a:6.1f}-{b:6.1f} s: heel (FSR5) made no ground contact ***{RESET}",
          flush=True)


# --------------------------------------------------------------------------
class HeelCheck:
    """Per-window check, fed one sample at a time (live and replay use the same code).

    Every sample, a match-case on the contact pattern records whether the
    heel (FSR5) touched down. At the end of each WINDOW_S window:
      - nothing loaded at all      -> "no contact" (sitting, foot in the air)
      - foot loaded, heel never    -> FLAG_LABEL
      - heel touched down          -> "normal"
    """

    def __init__(self):
        self.on = [False] * len(COLS)       # hysteresis state per sensor
        self.start = None
        self.any_seen = self.heel_seen = False

    def update(self, t, vals):
        """vals = 5 raw readings. Returns (t_start, t_end, verdict) at a window end, else None."""
        for i, v in enumerate(vals):
            if not self.on[i] and v > CONTACT_ON:
                self.on[i] = True
            elif self.on[i] and v < CONTACT_OFF:
                self.on[i] = False
        if self.start is None:
            self.start = t

        other = any(self.on[i] for i in range(len(COLS)) if i != HEEL)
        pattern = (2 if self.on[HEEL] else 0) | (1 if other else 0)
        match pattern:
            case 0:            # nothing loaded
                pass
            case 1:            # foot loaded, heel up: decide at the end of the window
                self.any_seen = True
            case 2 | 3:        # heel on the ground (alone or with the rest of the foot)
                self.any_seen = self.heel_seen = True

        if t - self.start >= WINDOW_S:
            if not self.any_seen:
                verdict = "no contact"
            else:
                verdict = "normal" if self.heel_seen else FLAG_LABEL
            result = (self.start, t, verdict)
            self.start, self.any_seen, self.heel_seen = t, False, False
            return result
        return None


# --------------------------------------------------------------------------
def make_figure():
    fig, axes = plt.subplots(5, 1, sharex=True, figsize=(11, 8))
    lines = []
    for ax, name, color in zip(axes, SENSORS, COLORS):
        (ln,) = ax.plot([], [], color=color, lw=1.5)
        ax.axhline(CONTACT_ON, color="#888888", ls="--", lw=0.8)
        ax.set_ylim(0, ADC_MAX * 1.02)
        ax.set_ylabel("ADC", color="#555555")
        ax.set_title(name, loc="left", fontsize=10)
        ax.grid(axis="y", color="#e5e5e5", lw=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        lines.append(ln)
    axes[-1].set_xlabel("time (s)")
    axes[0].text(1.0, 1.08, f"dashed line = contact threshold ({CONTACT_ON})",
                 transform=axes[0].transAxes, ha="right", fontsize=8, color="#555555")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return fig, axes, lines


def set_title(fig, flagged):
    if flagged:
        fig.suptitle(f"{FLAG_LABEL}: heel (FSR5) not contacting the ground",
                     color=FLAG_COLOR, fontweight="bold")
    else:
        fig.suptitle("Plantar pressure, 5 FSRs", color="#222222")


# --------------------------------------------------------------------------
def live(args):
    import serial
    from matplotlib.animation import FuncAnimation

    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, f"{args.participant}_{args.trial}_raw.csv")

    print(f"Opening {args.port} at {args.baud} baud...")
    ser = serial.Serial(args.port, args.baud, timeout=0)
    time.sleep(2)
    ser.reset_input_buffer()

    f = open(out_path, "w", newline="")
    w = csv.writer(f)
    w.writerow(["pc_time_s", "t_us"] + COLS)
    print(f"Recording to {out_path}. Close the plot window or Ctrl+C to stop.")

    n = int(args.window * SAMPLE_HZ) + SAMPLE_HZ
    t = collections.deque(maxlen=n)
    ch = [collections.deque(maxlen=n) for _ in COLS]
    buf = bytearray()
    st = {"t0": None, "ok": 0, "bad": 0, "start": time.time()}
    check = HeelCheck()

    fig, axes, lines = make_figure()
    set_title(fig, False)

    def update(_):
        buf.extend(ser.read(ser.in_waiting or 1))
        while b"\n" in buf:
            raw, _, rest = buf.partition(b"\n")
            buf[:] = rest
            line = raw.decode("ascii", errors="ignore").strip()
            if not line or line.startswith("t_us"):
                continue
            if line.startswith("#"):                      # status from the board, info only
                print(f"[board] {line.lstrip('#').strip()}", flush=True)
                continue
            parts = line.split(",")
            if len(parts) != 6:
                st["bad"] += 1
                continue
            try:
                vals = [int(p) for p in parts]
            except ValueError:
                st["bad"] += 1
                continue
            w.writerow([round(time.time() - st["start"], 4)] + vals)
            st["ok"] += 1
            if st["t0"] is None:
                st["t0"] = vals[0]
            ts = ((vals[0] - st["t0"]) & 0xFFFFFFFF) / 1e6   # survives micros() wrap
            t.append(ts)
            for i in range(5):
                ch[i].append(vals[i + 1])

            result = check.update(ts, vals[1:])
            if result:
                a, b, verdict = result
                if verdict == FLAG_LABEL:
                    print_flag(a, b)
                else:
                    print(f"[window] {a:6.1f}-{b:6.1f} s: {verdict}", flush=True)
                set_title(fig, verdict == FLAG_LABEL)
        if t:
            for ln, y in zip(lines, ch):
                ln.set_data(t, y)
            right = max(t[-1], args.window)
            axes[-1].set_xlim(right - args.window, right)
        return lines

    anim = FuncAnimation(fig, update, interval=50, cache_frame_data=False)  # noqa: F841
    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        f.close()
        print(f"Done: {st['ok']} samples, {st['bad']} bad lines skipped. Saved {out_path}")
        print(f"Next: python plot_fsr.py replay {out_path}")


# --------------------------------------------------------------------------
def replay(args):
    df = pd.read_csv(args.file, comment="#")
    missing = [c for c in ["t_us"] + COLS if c not in df.columns]
    if missing:
        sys.exit(f"Missing columns {missing} in {args.file}")
    t = (np.diff(df["t_us"].values, prepend=df["t_us"].values[0]) & 0xFFFFFFFF).cumsum() / 1e6
    data = df[COLS].values

    check = HeelCheck()
    windows = []
    for k in range(len(t)):
        result = check.update(t[k], data[k])
        if result:
            windows.append(result)

    n_flag = 0
    for a, b, v in windows:
        if v == FLAG_LABEL:
            n_flag += 1
            print_flag(a, b)
        else:
            print(f"[window] {a:6.1f}-{b:6.1f} s: {v}")
    print(f"{n_flag} of {len(windows)} windows flagged as {FLAG_LABEL.lower()}")

    fig, axes, lines = make_figure()
    for ln, c in zip(lines, COLS):
        ln.set_data(t, df[c].values)
    for a, b, v in windows:
        if v == FLAG_LABEL:
            for ax in axes:
                ax.axvspan(a, b, color=FLAG_COLOR, alpha=0.08, lw=0)
    axes[-1].set_xlim(0, max(t[-1], 1e-3))
    set_title(fig, n_flag > 0)
    if n_flag:
        axes[-1].text(0, -0.55, f"shaded = window flagged as {FLAG_LABEL.lower()}",
                      transform=axes[-1].transAxes, fontsize=8, color=FLAG_COLOR)
    if args.save:
        fig.savefig(args.save, dpi=150)
        print(f"Saved {args.save}")
    else:
        plt.show()


# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Plot 5 FSRs and flag when the heel never touches down")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("live")
    r.add_argument("--port", required=True)
    r.add_argument("--baud", type=int, default=115200)
    r.add_argument("--participant", default="P01")
    r.add_argument("--trial", default="walk1")
    r.add_argument("--outdir", default="data")
    r.add_argument("--window", type=float, default=10.0, help="seconds shown in the live plot")
    r.set_defaults(func=live)

    l = sub.add_parser("replay")
    l.add_argument("file")
    l.add_argument("--save", default=None, help="save the figure instead of showing it")
    l.set_defaults(func=replay)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
