"""
Reflection coefficient extractor.

Given a complex range profile and a calibration reference amplitude,
this module locates the soil-surface bin, extracts the complex
reflection coefficient Γ, and maintains a rolling average buffer.

Physical model (normal-incidence Fresnel, lossless approximation):
    Γ = (1 − √εr) / (1 + √εr)
    |Γ| = (√εr − 1) / (√εr + 1)   for εr > 1

Calibration reference: a metal plate (|Γ_metal| = 1) placed at the
same standoff distance normalises the measured amplitude:
    |Γ_soil| ≈ A_soil / A_metal
"""

from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from host.config import ChirpConfig, ProcessingConfig, CalibrationData


@dataclass
class ReflectionResult:
    amplitude: float          # raw |range_bin| before normalisation
    gamma_magnitude: float    # |Γ|, normalised by metal reference
    phase_rad: float          # angle of complex bin (relative, not absolute)
    soil_bin: int


class ReflectionExtractor:
    """
    Maintains a rolling buffer of range profiles and emits reflection
    coefficient estimates at a configurable averaging depth.
    """

    def __init__(
        self,
        chirp_cfg: ChirpConfig,
        proc_cfg: ProcessingConfig,
        cal: CalibrationData,
    ):
        self._chirp = chirp_cfg
        self._proc = proc_cfg
        self._cal = cal
        self._expected_bin = proc_cfg.soil_bin_index(chirp_cfg)
        self._buffer: deque[np.ndarray] = deque(maxlen=proc_cfg.num_frames_average)

    @property
    def expected_bin(self) -> int:
        return self._expected_bin

    def push_frame_profile(self, range_profile: np.ndarray) -> Optional[ReflectionResult]:
        """
        Accept one complex range profile (from compute_range_profile).
        Returns a ReflectionResult once the buffer is full, else None.
        """
        self._buffer.append(range_profile)
        if len(self._buffer) < self._proc.num_frames_average:
            return None
        return self._compute()

    def push_magnitude_profile(self, mag_profile: np.ndarray) -> Optional[float]:
        """
        UART path: accept log-magnitude uint16 range profile.
        Returns normalised amplitude (|Γ| proxy) once buffer is full.
        Linear amplitude = sqrt(10^(mag_u16 / 256 / 10)) – log2Q8 format.
        """
        linear = np.sqrt(np.power(10.0, mag_profile.astype(float) / 2560.0))
        self._buffer.append(linear)
        if len(self._buffer) < self._proc.num_frames_average:
            return None
        return self._compute_magnitude_only()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _best_bin(self, avg_profile: np.ndarray) -> int:
        tol = self._proc.soil_bin_tolerance
        lo = max(0, self._expected_bin - tol)
        hi = min(len(avg_profile) - 1, self._expected_bin + tol)
        local_mag = np.abs(avg_profile[lo: hi + 1])
        return lo + int(np.argmax(local_mag))

    def _compute(self) -> ReflectionResult:
        """Coherent average over buffered complex profiles."""
        stacked = np.stack(list(self._buffer), axis=0)  # (N, num_bins)
        avg = stacked.mean(axis=0)

        soil_bin = self._best_bin(avg)
        z = avg[soil_bin]
        amplitude = float(np.abs(z))
        phase = float(np.angle(z))
        gamma = amplitude / max(self._cal.metal_amplitude, 1e-12)
        gamma = float(np.clip(gamma, 0.0, 1.0))

        return ReflectionResult(
            amplitude=amplitude,
            gamma_magnitude=gamma,
            phase_rad=phase,
            soil_bin=soil_bin,
        )

    def _compute_magnitude_only(self) -> float:
        stacked = np.stack(list(self._buffer), axis=0)
        avg = stacked.mean(axis=0)
        soil_bin = self._best_bin(avg)
        amplitude = float(avg[soil_bin])
        return amplitude / max(self._cal.metal_amplitude, 1e-12)
