"""
mmWave soil moisture sensor – host-side entry point.

Usage:
  python -m host.main --cli-port COM4 --data-port COM5            # UART path
  python -m host.main --cli-port COM4 --mode dca1000              # DCA1000 path
  python -m host.main --cli-port COM4 --mode dca1000 --calibrate  # calibration workflow

HARDWARE SETUP (must be done manually before running):
  1. Flash IWR6843AOP with the out-of-box mmWave demo firmware
     (sdk_root/labs/out_of_box_demo or equivalent with LVDS streaming enabled).
  2. Identify CLI and data COM ports in Device Manager (XDS110 USB).
  3. For DCA1000 path: connect Ethernet, set host NIC to 192.168.33.30/24.
  4. Mount the sensor facing straight down at the soil -- standoff does not
     need to be fixed or known in advance, it's detected live every frame.
"""

import argparse
import csv
import logging
import sys
import time

import numpy as np

from host.config import ChirpConfig, ProcessingConfig
from host.capture.uart_interface import UARTInterface
from host.capture.dca1000 import DCA1000
from host.capture.frame_parser import FrameParser
from host.processing.range_fft import compute_range_profile
from host.processing.reflection import ReflectionExtractor, find_soil_peak_bin
from host.calibration.calibrate import CalibrationManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="IWR6843AOP soil moisture sensor – host pipeline"
    )
    p.add_argument("--cli-port", required=True, help="CLI UART COM port  (e.g. COM4)")
    p.add_argument("--data-port", default=None, help="Data UART COM port (UART path only)")
    p.add_argument(
        "--mode", choices=["dca1000", "uart"], default="dca1000",
        help="Data capture path"
    )
    p.add_argument("--config", default="config/chirp_config.cfg", help="Chirp config file")
    p.add_argument("--cal-file", default="config/calibration.json")
    p.add_argument("--avg-frames", type=int, default=10,
                   help="Frames to coherently average per VWC reading")
    p.add_argument("--output-csv", default=None, metavar="FILE",
                   help="Append timestamped VWC readings to this CSV file")
    p.add_argument("--calibrate", action="store_true",
                   help="Run interactive calibration workflow then exit")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


# ---------------------------------------------------------------------------
# DCA1000 measurement loop
# ---------------------------------------------------------------------------

def run_dca1000(args, chirp_cfg: ChirpConfig, proc_cfg: ProcessingConfig,
                cal_mgr: CalibrationManager, csv_writer) -> None:
    extractor = ReflectionExtractor(chirp_cfg, proc_cfg, cal_mgr.data)

    log.info(
        "DCA1000 path | soil-surface bin detected live each frame (skipping bins < %d) "
        "| range res=%.2f cm",
        proc_cfg.min_search_bin(chirp_cfg),
        chirp_cfg.range_resolution_m * 100,
    )

    with UARTInterface(args.cli_port) as uart:
        uart.send_config(args.config)

        with DCA1000() as dca:
            while True:
                try:
                    for frame_iq in dca.capture_frames(chirp_cfg, num_frames=1):
                        range_profile = compute_range_profile(frame_iq, proc_cfg.window)
                        result = extractor.push_frame_profile(range_profile)
                        if result is not None:
                            eps_r, vwc = cal_mgr.apply(result.gamma_magnitude)
                            _emit(vwc, eps_r, result, csv_writer)
                except KeyboardInterrupt:
                    log.info("Stopped by user.")
                    break


# ---------------------------------------------------------------------------
# UART measurement loop
# ---------------------------------------------------------------------------

def run_uart(args, chirp_cfg: ChirpConfig, proc_cfg: ProcessingConfig,
             cal_mgr: CalibrationManager, csv_writer) -> None:
    if not args.data_port:
        log.error("--data-port is required for UART mode")
        sys.exit(1)

    extractor = ReflectionExtractor(chirp_cfg, proc_cfg, cal_mgr.data)

    log.info(
        "UART path (magnitude-only) | soil-surface bin detected live each frame "
        "(skipping bins < %d)",
        proc_cfg.min_search_bin(chirp_cfg),
    )

    with UARTInterface(args.cli_port, args.data_port) as uart:
        uart.send_config(args.config)
        parser = FrameParser(uart)

        try:
            for frame in parser.iter_frames():
                if frame.range_profile is None:
                    continue
                result = extractor.push_magnitude_profile(frame.range_profile)
                if result is not None:
                    eps_r, vwc = cal_mgr.apply(result.gamma_magnitude)
                    log.info("VWC=%.4f m³/m³  εr=%.2f  |Γ|=%.3f  standoff=%.3fm",
                              vwc, eps_r, result.gamma_magnitude, result.standoff_m)
                    if csv_writer:
                        csv_writer.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"),
                                             f"{vwc:.4f}", f"{eps_r:.2f}",
                                             f"{result.gamma_magnitude:.4f}",
                                             str(result.soil_bin),
                                             f"{result.standoff_m:.4f}", "uart"])
        except KeyboardInterrupt:
            log.info("Stopped by user.")


# ---------------------------------------------------------------------------
# Calibration workflow
# ---------------------------------------------------------------------------

def _collect_samples(args, chirp_cfg: ChirpConfig, proc_cfg: ProcessingConfig,
                     num_samples: int):
    """Collect num_samples live (amplitude, standoff) readings via the DCA1000
    path and return their means. Standoff is detected live each sample -- the
    plate/soil does not need to be at any particular distance."""
    amps: list = []
    standoffs: list = []
    with UARTInterface(args.cli_port) as uart:
        uart.send_config(args.config)
        with DCA1000() as dca:
            for frame_iq in dca.capture_frames(chirp_cfg, num_frames=num_samples):
                profile = compute_range_profile(frame_iq, proc_cfg.window)
                linear = np.abs(profile)
                bin_idx = find_soil_peak_bin(linear, proc_cfg.min_search_bin(chirp_cfg))
                amps.append(float(linear[bin_idx]))
                standoffs.append(bin_idx * chirp_cfg.range_resolution_m)
                print(f"  sample {len(amps):3d}/{num_samples}  "
                      f"amplitude={amps[-1]:.1f}  standoff={standoffs[-1]:.3f}m", end="\r")
    print()
    return float(np.mean(amps)), float(np.mean(standoffs))


def run_calibration(args, chirp_cfg: ChirpConfig, proc_cfg: ProcessingConfig,
                    cal_mgr: CalibrationManager) -> None:
    """Interactive calibration wizard."""
    print("\n=== mmWave Soil Moisture Sensor – Calibration Wizard ===\n")

    # Step 1: metal plate, any distance -- repeatable for range-compensation fitting
    input("Step 1: Place a flat METAL PLATE at ANY distance under the sensor, "
          "then press Enter …")
    amp, standoff = _collect_samples(args, chirp_cfg, proc_cfg, num_samples=30)
    cal_mgr.add_metal_point(standoff, amp)
    print(f"  Metal point: standoff={standoff:.3f}m  amplitude={amp:.1f}")

    while True:
        ans = input(
            "Add another metal point at a DIFFERENT distance, to fit the "
            "range-compensation curve for this setup? (y/n) "
        ).strip().lower()
        if ans != "y":
            break
        input("  Move the plate to a different distance, then press Enter …")
        amp, standoff = _collect_samples(args, chirp_cfg, proc_cfg, num_samples=30)
        cal_mgr.add_metal_point(standoff, amp)
        print(f"  Metal point: standoff={standoff:.3f}m  amplitude={amp:.1f}  "
              f"(range_exponent={cal_mgr.data.range_exponent:.3f})")

    # Step 2: dry soil, any distance
    input("Step 2: Remove plate.  Ensure SOIL IS DRY, then press Enter …")
    amp, standoff = _collect_samples(args, chirp_cfg, proc_cfg, num_samples=20)
    cal_mgr.add_reference_point(amp, 0.0, standoff)

    # Step 3: additional reference points, any distance
    while True:
        ans = input("Step 3: Add another calibration point? (y/n) ").strip().lower()
        if ans != "y":
            break
        vwc_str = input("  Enter true VWC (m³/m³, e.g. 0.25): ").strip()
        try:
            vwc_val = float(vwc_str)
        except ValueError:
            print("  Invalid number, skipping.")
            continue
        input(f"  Prepare soil at VWC={vwc_val:.3f} m³/m³, then press Enter …")
        amp, standoff = _collect_samples(args, chirp_cfg, proc_cfg, num_samples=20)
        cal_mgr.add_reference_point(amp, vwc_val, standoff)

    # Step 4: optional polynomial fit
    if len(cal_mgr.data.calibration_points) >= 3:
        ans = input("Fit empirical polynomial (recommended if ≥3 points)? (y/n) ").strip().lower()
        if ans == "y":
            cal_mgr.fit_polynomial(degree=2)

    notes = input("Notes for this calibration session (optional): ").strip()
    cal_mgr.save(notes=notes)
    print("\nCalibration complete.")
    print(cal_mgr.summary())


# ---------------------------------------------------------------------------
# Output helper
# ---------------------------------------------------------------------------

def _emit(vwc: float, eps_r: float, result, csv_writer) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    log.info(
        "VWC=%.4f m³/m³  εr=%.2f  |Γ|=%.3f  phase=%.3f rad  bin=%d  standoff=%.3fm",
        vwc, eps_r, result.gamma_magnitude, result.phase_rad, result.soil_bin, result.standoff_m,
    )
    if csv_writer:
        csv_writer.writerow([
            ts, f"{vwc:.4f}", f"{eps_r:.2f}",
            f"{result.gamma_magnitude:.4f}", f"{result.phase_rad:.4f}",
            str(result.soil_bin), f"{result.standoff_m:.4f}", "dca1000",
        ])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = build_parser().parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    chirp_cfg = ChirpConfig()
    proc_cfg = ProcessingConfig(
        num_frames_average=args.avg_frames,
    )
    cal_mgr = CalibrationManager(args.cal_file)

    if not cal_mgr.data.is_ready():
        log.warning(
            "No calibration data loaded.  Run with --calibrate first for accurate results."
        )

    csv_file = None
    csv_writer = None
    if args.output_csv:
        csv_file = open(args.output_csv, "a", newline="")
        csv_writer = csv.writer(csv_file)
        if csv_file.tell() == 0:
            csv_writer.writerow(
                ["timestamp", "vwc_m3m3", "eps_r", "gamma", "phase_rad", "soil_bin", "standoff_m", "path"]
            )

    try:
        if args.calibrate:
            run_calibration(args, chirp_cfg, proc_cfg, cal_mgr)
        elif args.mode == "dca1000":
            run_dca1000(args, chirp_cfg, proc_cfg, cal_mgr, csv_writer)
        else:
            run_uart(args, chirp_cfg, proc_cfg, cal_mgr, csv_writer)
    finally:
        if csv_file:
            csv_file.close()


if __name__ == "__main__":
    main()
