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

Standoff is not assumed fixed: the metal-plate reference is stored as one
or more (standoff, amplitude) points and modeled as a function of distance
(amplitude = k / standoff^n), so a single calibration stays valid as the
sensor's distance to the soil changes -- e.g. a rover crossing uneven
ground. Two or more metal points at genuinely different standoffs let n be
fit empirically for this specific setup; with only one point, a
physically-motivated default (n=2, appropriate for a flat, beam-filling
reflector) is used instead.

Calibration JSON schema (config/calibration.json):
{
  "metal_calibration_points": [       // (standoff_m, amplitude) pairs
    {"standoff_m": 0.20, "amplitude": 12345.6},
    ...
  ],
  "range_exponent"    : <float>,    // amplitude = range_coeff_k / standoff_m**range_exponent
  "range_coeff_k"     : <float> | null,
  "calibration_points": [           // (amplitude, vwc_m3_per_m3, standoff_m) triples
    {"amplitude": 1234.5, "vwc": 0.02, "standoff_m": 0.20},
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

        if "metal_calibration_points" in raw:
            cal.metal_calibration_points = [
                (p["standoff_m"], p["amplitude"]) for p in raw["metal_calibration_points"]
            ]
        elif "metal_amplitude" in raw:
            # Migrating an older single-standoff calibration file: the real
            # standoff it was captured at isn't recorded in that schema, so
            # this is a best-effort guess, not a real measurement. Recalibrate
            # at at least one (ideally two+) real standoffs when convenient.
            log.warning(
                "Loaded legacy calibration.json (single metal_amplitude, no standoff "
                "recorded) - assuming 0.20m. Recalibrate for accurate range compensation."
            )
            cal.metal_calibration_points = [(0.20, raw["metal_amplitude"])]

        cal.range_exponent = raw.get("range_exponent", 2.0)
        cal.range_coeff_k = raw.get("range_coeff_k")
        if cal.range_coeff_k is None and cal.metal_calibration_points:
            cal.range_coeff_k = self._fit_range_compensation(cal)

        if raw.get("calibration_points") and isinstance(raw["calibration_points"][0], dict):
            cal.calibration_points = [
                (p["amplitude"], p["vwc"], p.get("standoff_m", 0.20))
                for p in raw["calibration_points"]
            ]
        coeffs = raw.get("poly_coeffs")
        cal.poly_coeffs = coeffs if coeffs is not None else None
        return cal

    def save(self, notes: str = "") -> None:
        payload = {
            "metal_calibration_points": [
                {"standoff_m": s, "amplitude": a} for s, a in self.data.metal_calibration_points
            ],
            "range_exponent": self.data.range_exponent,
            "range_coeff_k": self.data.range_coeff_k,
            "calibration_points": [
                {"amplitude": a, "vwc": v, "standoff_m": s}
                for a, v, s in self.data.calibration_points
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
    # Step 1: Metal-plate reference  (|Γ| = 1), any standoff
    # ------------------------------------------------------------------

    def add_metal_point(self, standoff_m: float, amplitude: float) -> None:
        """
        Record a metal-plate (|Γ|=1) reading at whatever standoff it was
        taken at, then refit the range-compensation model from all metal
        points collected so far.

        PROCEDURE:
          Place a flat metal plate (aluminium sheet ≥ 20 × 20 cm) under the
          sensor at ANY distance and collect a reading. Repeat at a second,
          genuinely different distance (and ideally a third) to let the
          real distance/amplitude falloff of this setup be fit empirically
          rather than assumed.
        """
        self.data.metal_calibration_points.append((standoff_m, amplitude))
        self.data.range_coeff_k = self._fit_range_compensation(self.data)
        log.info(
            "[CAL] Metal point added: standoff=%.4fm amplitude=%.2f "
            "(now %d point(s), range_exponent=%.3f, range_coeff_k=%.3f)",
            standoff_m, amplitude, len(self.data.metal_calibration_points),
            self.data.range_exponent, self.data.range_coeff_k,
        )

    @staticmethod
    def _fit_range_compensation(cal: CalibrationData) -> float:
        """
        Fit amplitude = k / standoff^n from the recorded metal points.

        With 2+ points at genuinely different standoffs, n is fit empirically
        via a log-log linear regression (this setup's real falloff behaviour).
        With only one point, cal.range_exponent is left at its physically-
        motivated default and only k is solved for.
        """
        points = cal.metal_calibration_points
        standoffs = np.array([s for s, _ in points], dtype=float)
        amps = np.array([a for _, a in points], dtype=float)

        if len(points) >= 2 and len(set(np.round(standoffs, 4))) >= 2:
            log_s = np.log(standoffs)
            log_a = np.log(np.clip(amps, 1e-12, None))
            slope, intercept = np.polyfit(log_s, log_a, 1)
            cal.range_exponent = float(-slope)
            k = float(np.exp(intercept))
        else:
            # Single standoff (or all identical): can't fit a slope, so keep
            # the default/current exponent and solve k from that one point.
            k = float(amps[-1] * standoffs[-1] ** cal.range_exponent)

        return k

    # ------------------------------------------------------------------
    # Step 2: Add reference points
    # ------------------------------------------------------------------

    def add_reference_point(self, amplitude: float, vwc_m3m3: float, standoff_m: float) -> None:
        """
        Record (amplitude, VWC, standoff) at a known soil moisture level.

        PROCEDURE:
          1. Prepare soil at a known VWC (measured gravimetrically or with a
             calibrated TDR / capacitance probe).
          2. Collect a reading at whatever standoff the sensor happens to be
             at -- it does not need to match previous points, since it's
             range-compensated automatically.
          Repeat for at least 3–5 moisture levels spanning your range of interest.
        """
        self.data.calibration_points.append((amplitude, vwc_m3m3, standoff_m))
        log.info(
            "[CAL] Added point: amplitude=%.2f  VWC=%.4f  standoff=%.4fm",
            amplitude, vwc_m3m3, standoff_m,
        )

    # ------------------------------------------------------------------
    # Step 3: Fit empirical model
    # ------------------------------------------------------------------

    def fit_polynomial(self, degree: int = 2) -> np.ndarray:
        """
        Fit a polynomial  VWC = p(amplitude)  from recorded reference points.

        Each point's amplitude is normalised against the range-compensated
        metal reference AT THAT POINT'S OWN standoff, so points collected at
        different distances are all comparable in Γ-space (0–1).

        Returns poly_coeffs as a list [a0, a1, …, a_degree] (lowest order first).
        """
        if len(self.data.calibration_points) < degree + 1:
            raise ValueError(
                f"Need at least {degree+1} calibration points for degree-{degree} fit, "
                f"have {len(self.data.calibration_points)}"
            )
        vwcs = np.array([p[1] for p in self.data.calibration_points])
        gammas = np.clip(
            [a / max(self.data.metal_amplitude_at(s), 1e-12) for a, _, s in self.data.calibration_points],
            0.0, 1.0,
        )
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
        d = self.data
        lines = [
            f"Metal points    : {len(d.metal_calibration_points)}",
        ]
        for standoff, amp in d.metal_calibration_points:
            lines.append(f"  standoff={standoff:.3f}m  amplitude={amp:.1f}")
        if d.range_coeff_k is not None:
            lines.append(f"Range model     : amplitude = {d.range_coeff_k:.2f} / standoff^{d.range_exponent:.3f}")
        else:
            lines.append("Range model     : none yet (add a metal point)")
        lines.append(f"Reference points: {len(d.calibration_points)}")
        for amp, vwc, standoff in d.calibration_points:
            gamma = amp / max(d.metal_amplitude_at(standoff), 1e-12)
            lines.append(f"  |Γ|={gamma:.3f}  VWC={vwc:.3f} m³/m³  (standoff={standoff:.3f}m)")
        if d.poly_coeffs:
            lines.append(f"Poly coeffs     : {d.poly_coeffs}")
        else:
            lines.append("Poly coeffs     : none (using physical model)")
        return "\n".join(lines)
