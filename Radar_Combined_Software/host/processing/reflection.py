"""
Reflection coefficient extractor.

Given a complex range profile and a calibration reference amplitude,
this module locates the soil-surface bin live (every frame -- standoff
is not assumed fixed), extracts the complex reflection coefficient Γ
compensated for that detected distance, and maintains a rolling average
buffer.

Physical model (normal-incidence Fresnel, lossless approximation):
    Γ = (1 − √εr) / (1 + √εr)
    |Γ| = (√εr − 1) / (√εr + 1)   for εr > 1

Calibration reference: a metal plate (|Γ_metal| = 1) placed at any
standoff, modeled as a function of distance via CalibrationData.metal_amplitude_at(),
normalises the measured amplitude:
    |Γ_soil| ≈ A_soil(standoff) / A_metal(standoff)
"""

from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from host.config import ChirpConfig, ProcessingConfig, CalibrationData


def find_soil_peak_bin(avg_profile: np.ndarray, min_search_bin: int) -> int:
    """Locate the strongest bin at or beyond min_search_bin, skipping the
    near-antenna-leakage bins below it."""
    lo = min(min_search_bin, len(avg_profile) - 1)
    local_mag = np.abs(avg_profile[lo:])
    return lo + int(np.argmax(local_mag))


@dataclass
class ReflectionResult:
    amplitude: float          # raw |range_bin| before normalisation
    gamma_magnitude: float    # |Γ|, range-compensated and normalised by metal reference
    phase_rad: float          # angle of complex bin (relative, not absolute)
    soil_bin: int
    standoff_m: float         # live-detected distance to the soil surface this frame


class ReflectionExtractor:
    """
    Maintains a rolling buffer of range profiles and emits reflection
    coefficient estimates at a configurable averaging depth. The soil-surface
    bin is re-detected on every averaged frame, so standoff can vary freely
    between (and even during) readings.
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
        self._buffer: deque[np.ndarray] = deque(maxlen=proc_cfg.num_frames_average)

    def push_frame_profile(self, range_profile: np.ndarray) -> Optional[ReflectionResult]:
        """
        Accept one complex range profile (from compute_range_profile).
        Returns a ReflectionResult once the buffer is full, else None.
        """
        self._buffer.append(range_profile)
        if len(self._buffer) < self._proc.num_frames_average:
            return None
        return self._compute()

    def push_magnitude_profile(self, mag_profile: np.ndarray) -> Optional[ReflectionResult]:
        """
        UART path: accept log-magnitude uint16 range profile.
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

    def _compute(self) -> ReflectionResult:
        """Coherent average over buffered complex profiles."""
        stacked = np.stack(list(self._buffer), axis=0)  # (N, num_bins)
        avg = stacked.mean(axis=0)

        soil_bin = find_soil_peak_bin(avg, self._proc.min_search_bin(self._chirp))
        standoff = soil_bin * self._chirp.range_resolution_m
        z = avg[soil_bin]
        amplitude = float(np.abs(z))
        phase = float(np.angle(z))
        gamma = amplitude / max(self._cal.metal_amplitude_at(standoff), 1e-12)
        gamma = float(np.clip(gamma, 0.0, 1.0))

        return ReflectionResult(
            amplitude=amplitude,
            gamma_magnitude=gamma,
            phase_rad=phase,
            soil_bin=soil_bin,
            standoff_m=standoff,
        )

    def _compute_magnitude_only(self) -> ReflectionResult:
        stacked = np.stack(list(self._buffer), axis=0)
        avg = stacked.mean(axis=0)
        soil_bin = find_soil_peak_bin(avg, self._proc.min_search_bin(self._chirp))
        standoff = soil_bin * self._chirp.range_resolution_m
        amplitude = float(avg[soil_bin])
        gamma = amplitude / max(self._cal.metal_amplitude_at(standoff), 1e-12)
        gamma = float(np.clip(gamma, 0.0, 1.0))

        return ReflectionResult(
            amplitude=amplitude,
            gamma_magnitude=gamma,
            phase_rad=0.0,
            soil_bin=soil_bin,
            standoff_m=standoff,
        )
