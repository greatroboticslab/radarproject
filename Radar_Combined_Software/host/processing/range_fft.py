"""
Range FFT for DCA1000 raw-ADC frames.

Input:  complex64 array, shape (num_chirps, num_samples, num_rx)
Output: complex64 range profile, shape (num_samples,)

Processing steps:
  1. Apply window across sample dimension.
  2. FFT along sample axis.
  3. Coherently sum across RX channels (virtual beamforming, broadside).
  4. Coherently average across chirps in the frame.
     (Valid because soil surface is stationary; incoherent avg would halve SNR.)
"""

import numpy as np
from numpy.fft import fft
from scipy.signal import get_window


def compute_range_profile(
    frame_iq: np.ndarray,
    window: str = "hann",
) -> np.ndarray:
    """
    Parameters
    ----------
    frame_iq : complex64, shape (num_chirps, num_samples, num_rx)
        Raw IQ samples for one frame from DCA1000.
    window : str
        SciPy window name applied along the sample axis.

    Returns
    -------
    complex64, shape (num_samples,)
        Coherently averaged complex range profile.
    """
    num_chirps, num_samples, num_rx = frame_iq.shape

    win = get_window(window, num_samples).astype(np.float32)
    # broadcast: (1, num_samples, 1)
    windowed = frame_iq * win[np.newaxis, :, np.newaxis]

    # FFT along sample axis → shape (num_chirps, num_samples, num_rx)
    range_bins = fft(windowed, axis=1)

    # Coherent RX combine: sum over RX axis → (num_chirps, num_samples)
    range_combined = range_bins.sum(axis=2)

    # Coherent average over chirps → (num_samples,)
    return range_combined.mean(axis=0).astype(np.complex64)


def range_bin_to_distance(bin_index: int, freq_slope_mhz_us: float,
                           adc_sample_rate_ksps: int) -> float:
    """Return the physical range (m) for a given FFT bin index."""
    from host.config import C_LIGHT
    slope_hz_per_s = freq_slope_mhz_us * 1e12
    sample_rate_hz = adc_sample_rate_ksps * 1e3
    range_resolution = (sample_rate_hz * C_LIGHT) / (2.0 * slope_hz_per_s * adc_sample_rate_ksps)
    # Simpler: range_resolution = c / (2 * BW); bin_range = bin * range_resolution
    # Use the per-bin formula directly:
    return bin_index * (C_LIGHT * sample_rate_hz) / (2.0 * slope_hz_per_s)
