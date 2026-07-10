"""
Calibration workflow for the mmWave soil moisture sensor.

Two calibration models are supported:

  Physical model (default):
    Uses Fresnel formula + Topp's equation with only the metal-plate
    reference amplitude.  Works well when εr is in the Topp valid range
    and surface roughness is low.  Requires one metal-plate measurement.

  Empirical regression:
    Polynomial fit from measured amplitude → VWC, built from multiple
    reference points with gravimetrically or TDR-verified VWC.
    Use this when the physical model shows systematic bias.

Calibration JSON schema (config/calibration.json):
{
  "metal_amplitude"   : <float>,    // amplitude with metal plate at standoff
  "calibration_points": [           // (amplitude, vwc_m3_per_m3) pairs
    {"amplitude": 1234.5, "vwc": 0.02},
    ...
  ],
  "poly_coeffs"       : [a0, a1, ...] | null,
  "notes"             : "<string>",
  "created_at"        : "<ISO-8601>"
}
"""

import json
import logging
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
from scipy.optimize import curve_fit

from host.config import CalibrationData, ChirpConfig, ProcessingConfig
from host.processing.reflection import ReflectionExtractor
from host.processing.vwc import gamma_to_vwc

log = logging.getLogger(__name__)


class CalibrationManager:
    def __init__(self, cal_path: str):
        self._path = Path(cal_path)
        self.data = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> CalibrationData:
        if not self._path.exists():
            log.info("No calibration file found at %s – using defaults", self._path)
            return CalibrationData()
        with self._path.open() as fh:
            raw = json.load(fh)
        cal = CalibrationData()
        cal.metal_amplitude = raw.get("metal_amplitude", 1.0)
        cal.calibration_points = [
            (p["amplitude"], p["vwc"]) for p in raw.get("calibration_points", [])
        ]
        coeffs = raw.get("poly_coeffs")
        cal.poly_coeffs = coeffs if coeffs is not None else None
        return cal

    def save(self, notes: str = "") -> None:
        payload = {
            "metal_amplitude": self.data.metal_amplitude,
            "calibration_points": [
                {"amplitude": a, "vwc": v} for a, v in self.data.calibration_points
            ],
            "poly_coeffs": self.data.poly_coeffs,
            "notes": notes,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w") as fh:
            json.dump(payload, fh, indent=2)
        log.info("Calibration saved to %s", self._path)

    # ------------------------------------------------------------------
    # Step 1: Metal-plate reference  (|Γ| = 1)
    # ------------------------------------------------------------------

    def run_metal_calibration(
        self,
        amplitude_provider: Callable[[], float],
        num_samples: int = 20,
    ) -> float:
        """
        Collect `num_samples` amplitude readings with a metal plate at standoff.

        Parameters
        ----------
        amplitude_provider : callable
            A zero-argument callable that returns the current raw amplitude
            (|complex range bin|) from the extractor.  The caller is responsible
            for pumping the data pipeline and calling this after each frame.
        num_samples : int
            Number of readings to average.

        Returns
        -------
        float
            Measured metal-plate amplitude stored in calibration data.

        PROCEDURE:
          1. Place a flat metal plate (aluminium sheet ≥ 20 × 20 cm) at the
             same standoff distance the sensor will face during measurement.
          2. Call this method.  The console will prompt frame-by-frame.
          3. Remove the plate and save().
        """
        print(f"\n[CAL] Metal-plate calibration – collecting {num_samples} samples …")
        readings: List[float] = []
        for i in range(num_samples):
            amp = amplitude_provider()
            readings.append(amp)
            print(f"  sample {i+1:3d}/{num_samples}  amplitude={amp:.1f}", end="\r")
        print()
        metal_amp = float(np.mean(readings))
        self.data.metal_amplitude = metal_amp
        log.info("[CAL] Metal amplitude = %.2f (std=%.2f)", metal_amp, np.std(readings))
        return metal_amp

    # ------------------------------------------------------------------
    # Step 2: Add reference points
    # ------------------------------------------------------------------

    def add_reference_point(
        self,
        amplitude_provider: Callable[[], float],
        vwc_m3m3: float,
        num_samples: int = 20,
    ) -> None:
        """
        Record (amplitude, VWC) at a known soil moisture level.

        PROCEDURE:
          1. Prepare soil at a known VWC (measured gravimetrically or with a
             calibrated TDR / capacitance probe).
          2. Place the sensor at its working standoff.
          3. Call this method with the true VWC value.
          Repeat for at least 3–5 moisture levels spanning your range of interest.
        """
        label = f"VWC={vwc_m3m3:.3f} m³/m³"
        print(f"\n[CAL] Reference point {label} – collecting {num_samples} samples …")
        readings: List[float] = []
        for i in range(num_samples):
            amp = amplitude_provider()
            readings.append(amp)
            print(f"  sample {i+1:3d}/{num_samples}  amplitude={amp:.1f}", end="\r")
        print()
        mean_amp = float(np.mean(readings))
        self.data.calibration_points.append((mean_amp, vwc_m3m3))
        log.info("[CAL] Added point: amplitude=%.2f  VWC=%.4f", mean_amp, vwc_m3m3)

    # ------------------------------------------------------------------
    # Step 3: Fit empirical model
    # ------------------------------------------------------------------

    def fit_polynomial(self, degree: int = 2) -> np.ndarray:
        """
        Fit a polynomial  VWC = p(amplitude)  from recorded reference points.

        The fit amplitude is normalised by metal_amplitude first so the
        polynomial is in Γ-space (0–1), making it more transferable across
        sensor units.

        Returns poly_coeffs as a list [a0, a1, …, a_degree] (lowest order first).
        """
        if len(self.data.calibration_points) < degree + 1:
            raise ValueError(
                f"Need at least {degree+1} calibration points for degree-{degree} fit, "
                f"have {len(self.data.calibration_points)}"
            )
        amps = np.array([p[0] for p in self.data.calibration_points])
        vwcs = np.array([p[1] for p in self.data.calibration_points])
        gammas = np.clip(amps / max(self.data.metal_amplitude, 1e-12), 0.0, 1.0)
        coeffs = np.polyfit(gammas, vwcs, degree)[::-1].tolist()  # lowest order first
        self.data.poly_coeffs = coeffs
        log.info("[CAL] Polynomial fit (degree %d): %s", degree, coeffs)
        return np.array(coeffs)

    # ------------------------------------------------------------------
    # Apply calibration
    # ------------------------------------------------------------------

    def apply(self, gamma: float) -> Tuple[float, float]:
        """
        Convert |Γ| to (εr, VWC), using empirical polynomial if fitted,
        else the physical Fresnel + Topp model.

        Returns
        -------
        (eps_r, vwc) : tuple[float, float]
        """
        if self.data.poly_coeffs is not None:
            coeffs = np.array(self.data.poly_coeffs)
            vwc = float(np.polyval(coeffs[::-1], gamma))
            vwc = float(np.clip(vwc, 0.0, 0.6))
            from host.processing.vwc import gamma_to_permittivity
            eps_r = gamma_to_permittivity(gamma)
            return eps_r, vwc

        return gamma_to_vwc(gamma)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = [
            f"Metal amplitude : {self.data.metal_amplitude:.2f}",
            f"Reference points: {len(self.data.calibration_points)}",
        ]
        for amp, vwc in self.data.calibration_points:
            gamma = amp / max(self.data.metal_amplitude, 1e-12)
            lines.append(f"  |Γ|={gamma:.3f}  VWC={vwc:.3f} m³/m³")
        if self.data.poly_coeffs:
            lines.append(f"Poly coeffs     : {self.data.poly_coeffs}")
        else:
            lines.append("Poly coeffs     : none (using physical model)")
        return "\n".join(lines)
