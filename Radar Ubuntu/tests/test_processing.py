"""
Unit tests for the signal-processing and VWC-conversion pipeline.
All tests use synthetic data and do not require hardware.

Run:
  python -m pytest tests/ -v
"""

import numpy as np
import pytest

from host.config import ChirpConfig, ProcessingConfig, CalibrationData
from host.processing.range_fft import compute_range_profile
from host.processing.reflection import ReflectionExtractor
from host.processing.vwc import (
    gamma_to_permittivity,
    permittivity_to_vwc,
    gamma_to_vwc,
    topp_invert,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_synthetic_frame(
    chirp_cfg: ChirpConfig,
    target_bin: int,
    amplitude: float = 1000.0,
    noise_std: float = 10.0,
) -> np.ndarray:
    """Return a synthetic IQ frame with a single range target at target_bin."""
    num_chirps = chirp_cfg.num_chirps_per_frame
    num_samples = chirp_cfg.num_adc_samples
    num_rx = chirp_cfg.num_rx

    rng = np.random.default_rng(seed=42)
    noise = (
        rng.normal(0, noise_std, (num_chirps, num_samples, num_rx))
        + 1j * rng.normal(0, noise_std, (num_chirps, num_samples, num_rx))
    ).astype(np.complex64)

    # Inject a pure tone at target_bin frequency into each sample dimension
    t = np.arange(num_samples)
    phase_per_sample = 2 * np.pi * target_bin / num_samples
    tone = (amplitude * np.exp(1j * phase_per_sample * t)).astype(np.complex64)
    frame = noise.copy()
    frame[:, :, :] += tone[np.newaxis, :, np.newaxis]
    return frame


# ---------------------------------------------------------------------------
# Range FFT tests
# ---------------------------------------------------------------------------

class TestRangeFFT:
    def test_target_detected_at_correct_bin(self):
        cfg = ChirpConfig()
        target_bin = 5
        frame = make_synthetic_frame(cfg, target_bin, amplitude=5000.0, noise_std=5.0)
        profile = compute_range_profile(frame)
        assert profile.shape == (cfg.num_adc_samples,)
        detected_bin = int(np.argmax(np.abs(profile)))
        assert detected_bin == target_bin

    def test_output_dtype(self):
        cfg = ChirpConfig()
        frame = make_synthetic_frame(cfg, target_bin=3)
        profile = compute_range_profile(frame)
        assert profile.dtype == np.complex64

    def test_noise_only_no_dominant_bin(self):
        cfg = ChirpConfig()
        rng = np.random.default_rng(0)
        noise_frame = (
            rng.normal(0, 1, (cfg.num_chirps_per_frame, cfg.num_adc_samples, cfg.num_rx))
            + 1j * rng.normal(0, 1, (cfg.num_chirps_per_frame, cfg.num_adc_samples, cfg.num_rx))
        ).astype(np.complex64)
        profile = compute_range_profile(noise_frame)
        # No bin should be more than 4× the RMS of the profile
        mag = np.abs(profile)
        assert mag.max() < 4 * mag.mean() * np.sqrt(cfg.num_adc_samples)


# ---------------------------------------------------------------------------
# VWC conversion tests
# ---------------------------------------------------------------------------

class TestVWCConversion:
    @pytest.mark.parametrize("gamma,expected_eps_approx", [
        (0.0,  1.0),   # air – no reflection → εr = 1
        (0.5,  9.0),   # εr = ((1+0.5)/(1-0.5))² = 9
        (0.8, 81.0),   # εr = ((1+0.8)/(1-0.8))² = 81
    ])
    def test_gamma_to_permittivity(self, gamma, expected_eps_approx):
        eps = gamma_to_permittivity(gamma)
        assert abs(eps - expected_eps_approx) < 0.01

    def test_permittivity_to_vwc_dry(self):
        # εr ≈ 3–4 for dry sand → VWC near 0
        vwc = permittivity_to_vwc(3.5)
        assert 0.0 <= vwc < 0.05

    def test_permittivity_to_vwc_wet(self):
        # εr ≈ 25 → VWC ≈ 0.35
        vwc = permittivity_to_vwc(25.0)
        assert 0.25 < vwc < 0.45

    def test_vwc_clamped_to_zero(self):
        # Very low εr should not produce negative VWC
        vwc = permittivity_to_vwc(1.0)
        assert vwc == 0.0

    def test_topp_invert_roundtrip(self):
        for target_vwc in [0.05, 0.15, 0.25, 0.35]:
            eps = topp_invert(target_vwc)
            assert not np.isnan(eps), f"invert failed for vwc={target_vwc}"
            recovered = permittivity_to_vwc(eps)
            assert abs(recovered - target_vwc) < 1e-4, (
                f"roundtrip failed: target={target_vwc}, recovered={recovered}"
            )


# ---------------------------------------------------------------------------
# Reflection extractor tests
# ---------------------------------------------------------------------------

class TestReflectionExtractor:
    def _make_extractor(self, standoff: float = 0.20) -> ReflectionExtractor:
        chirp_cfg = ChirpConfig()
        proc_cfg = ProcessingConfig(standoff_m=standoff, num_frames_average=3)
        cal = CalibrationData(metal_amplitude=10_000.0)
        return ReflectionExtractor(chirp_cfg, proc_cfg, cal)

    def test_returns_none_until_buffer_full(self):
        ext = self._make_extractor()
        cfg = ChirpConfig()
        profile = np.zeros(cfg.num_adc_samples, dtype=np.complex64)
        assert ext.push_frame_profile(profile) is None
        assert ext.push_frame_profile(profile) is None
        result = ext.push_frame_profile(profile)
        assert result is not None

    def test_gamma_normalised(self):
        ext = self._make_extractor(standoff=0.20)
        cfg = ChirpConfig()
        proc = ProcessingConfig(standoff_m=0.20, num_frames_average=3)
        expected_bin = proc.soil_bin_index(cfg)

        # Inject amplitude equal to metal reference at the expected bin
        profile = np.zeros(cfg.num_adc_samples, dtype=np.complex64)
        profile[expected_bin] = 10_000.0 + 0j
        for _ in range(3):
            result = ext.push_frame_profile(profile)
        assert result is not None
        assert abs(result.gamma_magnitude - 1.0) < 0.01

    def test_expected_bin_matches_standoff(self):
        cfg = ChirpConfig()
        for standoff in [0.10, 0.20, 0.30]:
            proc = ProcessingConfig(standoff_m=standoff)
            expected = proc.soil_bin_index(cfg)
            expected_range = expected * cfg.range_resolution_m
            assert abs(expected_range - standoff) <= cfg.range_resolution_m
