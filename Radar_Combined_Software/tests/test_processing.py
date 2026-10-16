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
    def _make_extractor(self, num_frames_average: int = 3, range_exponent: float = 0.0,
                        range_coeff_k: float = 10_000.0) -> ReflectionExtractor:
        # range_exponent=0 makes metal_amplitude_at() flat regardless of
        # distance, isolating bin-detection/gamma-normalisation from range
        # compensation (which has its own tests below).
        chirp_cfg = ChirpConfig()
        proc_cfg = ProcessingConfig(num_frames_average=num_frames_average)
        cal = CalibrationData(range_exponent=range_exponent, range_coeff_k=range_coeff_k)
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
        ext = self._make_extractor()
        cfg = ChirpConfig()
        target_bin = 10  # safely beyond the default near-field exclusion zone

        # Inject amplitude equal to the (flat) metal reference at any bin
        profile = np.zeros(cfg.num_adc_samples, dtype=np.complex64)
        profile[target_bin] = 10_000.0 + 0j
        for _ in range(3):
            result = ext.push_frame_profile(profile)
        assert result is not None
        assert abs(result.gamma_magnitude - 1.0) < 0.01

    def test_live_peak_detection_finds_target_bin(self):
        """The soil-surface bin (and therefore standoff) is detected live
        from the profile itself -- no configured/fixed standoff is used."""
        ext = self._make_extractor()
        cfg = ChirpConfig()
        target_bin = 8

        profile = np.zeros(cfg.num_adc_samples, dtype=np.complex64)
        profile[target_bin] = 5000.0 + 0j
        for _ in range(3):
            result = ext.push_frame_profile(profile)
        assert result is not None
        assert result.soil_bin == target_bin
        assert abs(result.standoff_m - target_bin * cfg.range_resolution_m) < 1e-9

    def test_min_search_bin_excludes_near_field(self):
        """Bins within the near-field boundary (leakage) must never be picked
        as the soil bin, even if they have higher amplitude than a real,
        more distant target -- this is what actually broke in practice: a
        naive global-max search kept locking onto leakage."""
        chirp_cfg = ChirpConfig()
        # ~2 bins' worth of exclusion at this chirp config's range resolution
        proc_cfg = ProcessingConfig(num_frames_average=1, near_field_boundary_m=0.09)
        assert proc_cfg.min_search_bin(chirp_cfg) == 2
        cal = CalibrationData(range_exponent=0.0, range_coeff_k=10_000.0)
        ext = ReflectionExtractor(chirp_cfg, proc_cfg, cal)

        profile = np.zeros(chirp_cfg.num_adc_samples, dtype=np.complex64)
        profile[0] = 50_000.0 + 0j   # strong leakage bin -- must be ignored
        profile[6] = 3_000.0 + 0j    # real target
        result = ext.push_frame_profile(profile)
        assert result is not None
        assert result.soil_bin == 6

    def test_default_near_field_boundary_clears_leakage_plateau(self):
        """Regression test for a real bug: with the default 0.30m boundary,
        near-field leakage spanning multiple bins near the antenna (not just
        bin 0) must not be picked over a genuine, weaker target further out."""
        chirp_cfg = ChirpConfig()
        proc_cfg = ProcessingConfig(num_frames_average=1)  # default near_field_boundary_m
        min_bin = proc_cfg.min_search_bin(chirp_cfg)
        assert min_bin > 2, "default boundary should exclude more than just bins 0-1"

        cal = CalibrationData(range_exponent=0.0, range_coeff_k=10_000.0)
        ext = ReflectionExtractor(chirp_cfg, proc_cfg, cal)

        profile = np.zeros(chirp_cfg.num_adc_samples, dtype=np.complex64)
        # A leakage plateau spanning bins 0 through min_bin-1, stronger than
        # the real target -- must be fully excluded, not just bin 0.
        for b in range(min_bin):
            profile[b] = 20_000.0 + 0j
        real_target_bin = min_bin + 3
        profile[real_target_bin] = 1_000.0 + 0j

        result = ext.push_frame_profile(profile)
        assert result is not None
        assert result.soil_bin == real_target_bin


# ---------------------------------------------------------------------------
# Range compensation tests (standoff-independent calibration)
# ---------------------------------------------------------------------------

class TestRangeCompensation:
    def test_metal_amplitude_at_uses_power_law(self):
        cal = CalibrationData(range_exponent=2.0, range_coeff_k=100.0)
        assert abs(cal.metal_amplitude_at(1.0) - 100.0) < 1e-9
        assert abs(cal.metal_amplitude_at(2.0) - 25.0) < 1e-9  # 100 / 2**2

    def test_no_calibration_yet_returns_flat_one(self):
        cal = CalibrationData()
        assert cal.metal_amplitude_at(0.3) == 1.0

    def test_single_metal_point_uses_default_exponent(self, tmp_path):
        from host.calibration.calibrate import CalibrationManager
        mgr = CalibrationManager(str(tmp_path / "cal.json"))
        mgr.add_metal_point(0.20, 1000.0)
        assert mgr.data.range_exponent == 2.0  # default, unchanged with 1 point
        # k solved so the single point round-trips exactly
        assert abs(mgr.data.metal_amplitude_at(0.20) - 1000.0) < 1e-6

    def test_two_metal_points_fit_exponent_empirically(self, tmp_path):
        from host.calibration.calibrate import CalibrationManager
        mgr = CalibrationManager(str(tmp_path / "cal.json"))
        # Synthetic amplitude ~ k / standoff^3 (n=3, not the n=2 default)
        true_k, true_n = 500.0, 3.0
        for standoff in (0.15, 0.40):
            amp = true_k / standoff ** true_n
            mgr.add_metal_point(standoff, amp)
        assert abs(mgr.data.range_exponent - true_n) < 1e-6
        assert abs(mgr.data.range_coeff_k - true_k) / true_k < 1e-6
        # And a third, untested distance should also predict correctly
        predicted = mgr.data.metal_amplitude_at(0.25)
        expected = true_k / 0.25 ** true_n
        assert abs(predicted - expected) / expected < 1e-6
