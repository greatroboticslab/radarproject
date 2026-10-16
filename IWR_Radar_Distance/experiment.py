"""
Scripted data collection for the box-material distance experiment.

  python experiment.py configure              # send cfg, leave sensor running, settle/calibrate
  python experiment.py trial --label metal --height-mm 135 --trial 1
  python experiment.py stop                   # sensorStop

The sensor keeps streaming between trials; each trial just opens the data port
for N seconds, so the DC-range calibration done at configure time (empty desk)
stays valid for the whole session.

Output (logs/experiment/):
  <label>_t<n>_<stamp>_points.csv   every detected point, every frame
  <label>_t<n>_<stamp>_profile.csv  frame-averaged range profile
  summary.csv                       one row per trial, appended
"""

import argparse
import csv
import math
import os
import statistics
import sys
import time

import serial

from radar_reader import FrameParser, RadarConnection, list_serial_ports

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "logs", "experiment")
DEFAULT_CFG = os.path.join(HERE, "default_config.cfg")
Q9_LOG2_TO_DB = 20 * math.log10(2) / 512

SUMMARY_FIELDS = [
    "label", "trial", "timestamp", "seconds", "frames", "mount_height_m",
    "object_height_mm", "expected_m", "window_m", "detection_rate",
    "mean_m", "median_m", "std_m", "min_m", "max_m", "mean_error_m",
    "mean_snr_db", "nearest_mean_m", "nearest_std_m",
    "profile_peak_db", "profile_peak_range_m", "points_file",
]


def auto_port(keyword, fallback):
    for p in list_serial_ports():
        if keyword in p.description.lower():
            return p.device
    return fallback


def range_bin_size_m(cfg_path):
    with open(cfg_path) as f:
        for line in f:
            parts = line.split()
            if parts and parts[0] == "profileCfg":
                slope_hz_per_s = float(parts[8]) * 1e12
                n_samples = int(parts[10])
                fs_hz = float(parts[11]) * 1e3
                n_fft = 1 << (n_samples - 1).bit_length()
                return 3e8 * fs_hz / (2 * slope_hz_per_s * n_fft)
    raise ValueError(f"No profileCfg line in {cfg_path}")


def capture(data_port, seconds):
    ser = serial.Serial(data_port, 921600, timeout=0.1)
    ser.reset_input_buffer()
    parser = FrameParser()
    frames = []
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            data = ser.read(4096)
            if data:
                now = time.time()
                frames.extend((now, fr) for fr in parser.feed(data))
    finally:
        ser.close()
    return frames


def cmd_configure(args):
    conn = RadarConnection(args.cli, args.data)
    conn.send_config(args.cfg)
    conn.cli_ser.close()  # leave the sensor running

    failed = False
    while not conn.log_queue.empty():
        msg = conn.log_queue.get_nowait()
        if "WARNING" in msg:
            failed = True
            print(msg)
    if failed:
        print("CONFIG FAILED -- see rejected lines above.")
        sys.exit(1)

    print(f"Sensor started. Settling {args.settle}s for DC calibration -- keep the area under the radar EMPTY.")
    frames = capture(args.data, args.settle)
    if not frames:
        print("ERROR: no frames received on data port.")
        sys.exit(1)
    print(f"READY: received {len(frames)} frames during settle.")


def cmd_stop(args):
    ser = serial.Serial(args.cli, 115200, timeout=1)
    ser.write(b"sensorStop\n")
    time.sleep(0.3)
    print(ser.read(ser.in_waiting or 1).decode(errors="replace").strip())
    ser.close()


def cmd_trial(args):
    os.makedirs(OUT_DIR, exist_ok=True)
    bin_m = range_bin_size_m(args.cfg)
    expected = args.mount_m - args.height_mm / 1000.0

    print(f"Capturing '{args.label}' trial {args.trial} for {args.seconds}s (expected {expected:.3f} m)...")
    frames = capture(args.data, args.seconds)
    if not frames:
        print("ERROR: no frames received.")
        sys.exit(1)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = f"{args.label}_t{args.trial}_{stamp}"
    points_path = os.path.join(OUT_DIR, base + "_points.csv")
    profile_path = os.path.join(OUT_DIR, base + "_profile.csv")

    target_ranges, target_snrs, nearest = [], [], []
    profile_sum, profile_n = None, 0

    with open(points_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "trial", "wall_time", "frame_number", "x_m", "y_m", "z_m",
                    "velocity_mps", "range_m", "snr_db", "noise_db"])
        for t, fr in frames:
            best = None  # highest-SNR point inside the expected-distance window
            for i, (x, y, z, v) in enumerate(fr.points):
                r = math.sqrt(x * x + y * y + z * z)
                snr = fr.snr_db[i] if i < len(fr.snr_db) else None
                noise = fr.noise_db[i] if i < len(fr.noise_db) else None
                w.writerow([args.label, args.trial, f"{t:.3f}", fr.frame_number,
                            f"{x:.4f}", f"{y:.4f}", f"{z:.4f}", f"{v:.4f}", f"{r:.4f}",
                            "" if snr is None else snr, "" if noise is None else noise])
                if abs(r - expected) <= args.window and (best is None or (snr or 0) > (best[1] or 0)):
                    best = (r, snr)
            if best is not None:
                target_ranges.append(best[0])
                if best[1] is not None:
                    target_snrs.append(best[1])
            if fr.min_range_m is not None:
                nearest.append(fr.min_range_m)
            if fr.range_profile:
                if profile_sum is None:
                    profile_sum = [0.0] * len(fr.range_profile)
                if len(fr.range_profile) == len(profile_sum):
                    profile_sum = [a + b for a, b in zip(profile_sum, fr.range_profile)]
                    profile_n += 1

    peak_db = peak_range = None
    if profile_n:
        profile_db = [s / profile_n * Q9_LOG2_TO_DB for s in profile_sum]
        with open(profile_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["bin", "range_m", "mean_db"])
            for i, db in enumerate(profile_db):
                w.writerow([i, f"{i * bin_m:.4f}", f"{db:.3f}"])
        center = round(expected / bin_m)
        lo, hi = max(center - 3, 0), min(center + 3, len(profile_db) - 1)
        peak_bin = max(range(lo, hi + 1), key=lambda i: profile_db[i])
        peak_db, peak_range = profile_db[peak_bin], peak_bin * bin_m

    def fmt(v, nd=4):
        return "" if v is None else f"{v:.{nd}f}"

    n_frames = len(frames)
    mean_r = statistics.fmean(target_ranges) if target_ranges else None
    row = {
        "label": args.label, "trial": args.trial, "timestamp": stamp,
        "seconds": args.seconds, "frames": n_frames, "mount_height_m": args.mount_m,
        "object_height_mm": args.height_mm, "expected_m": fmt(expected),
        "window_m": args.window,
        "detection_rate": fmt(len(target_ranges) / n_frames, 3),
        "mean_m": fmt(mean_r),
        "median_m": fmt(statistics.median(target_ranges) if target_ranges else None),
        "std_m": fmt(statistics.stdev(target_ranges) if len(target_ranges) > 1 else None),
        "min_m": fmt(min(target_ranges) if target_ranges else None),
        "max_m": fmt(max(target_ranges) if target_ranges else None),
        "mean_error_m": fmt(mean_r - expected if mean_r is not None else None),
        "mean_snr_db": fmt(statistics.fmean(target_snrs) if target_snrs else None, 2),
        "nearest_mean_m": fmt(statistics.fmean(nearest) if nearest else None),
        "nearest_std_m": fmt(statistics.stdev(nearest) if len(nearest) > 1 else None),
        "profile_peak_db": fmt(peak_db, 2),
        "profile_peak_range_m": fmt(peak_range),
        "points_file": os.path.basename(points_path),
    }

    summary_path = os.path.join(OUT_DIR, "summary.csv")
    new_file = not os.path.exists(summary_path)
    with open(summary_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)

    print(f"  frames={n_frames}  detected in window={row['detection_rate']}")
    print(f"  measured mean={row['mean_m']} m  median={row['median_m']}  std={row['std_m']}  error={row['mean_error_m']} m")
    print(f"  mean SNR={row['mean_snr_db']} dB  range-profile peak={row['profile_peak_db']} dB @ {row['profile_peak_range_m']} m")
    print(f"  nearest-object mean={row['nearest_mean_m']} m (std {row['nearest_std_m']})")
    print(f"  saved {os.path.basename(points_path)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", default=None)
    ap.add_argument("--data", default=None)
    ap.add_argument("--cfg", default=DEFAULT_CFG)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("configure")
    c.add_argument("--settle", type=float, default=30.0)

    sub.add_parser("stop")

    t = sub.add_parser("trial")
    t.add_argument("--label", required=True)
    t.add_argument("--trial", type=int, required=True)
    t.add_argument("--height-mm", type=float, default=0.0)
    t.add_argument("--mount-m", type=float, default=0.51)
    t.add_argument("--seconds", type=float, default=15.0)
    t.add_argument("--window", type=float, default=0.10)

    args = ap.parse_args()
    args.cli = args.cli or auto_port("enhanced", "COM8")
    args.data = args.data or auto_port("standard", "COM9")

    {"configure": cmd_configure, "stop": cmd_stop, "trial": cmd_trial}[args.cmd](args)


if __name__ == "__main__":
    main()
