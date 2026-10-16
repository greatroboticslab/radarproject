from dataclasses import dataclass, field
from typing import List, Optional, Tuple

C_LIGHT = 3e8  # m/s


@dataclass
class ChirpConfig:
    """Mirrors the profileCfg / frameCfg parameters sent to the radar CLI."""
    start_freq_ghz: float = 60.0
    freq_slope_mhz_us: float = 166.0   # MHz/µs (freqSlopeConst)
    idle_time_us: float = 7.0
    adc_start_time_us: float = 3.0
    ramp_end_time_us: float = 24.0
    num_adc_samples: int = 256
    adc_sample_rate_ksps: int = 12500
    num_chirps_per_frame: int = 32
    frame_period_ms: float = 100.0
    num_rx: int = 4
    num_tx: int = 3

    @property
    def sweep_time_us(self) -> float:
        return self.ramp_end_time_us - self.adc_start_time_us

    @property
    def bandwidth_hz(self) -> float:
        return self.freq_slope_mhz_us * self.sweep_time_us * 1e6

    @property
    def range_resolution_m(self) -> float:
        return C_LIGHT / (2.0 * self.bandwidth_hz)

    @property
    def max_range_m(self) -> float:
        """Maximum unambiguous range from range-FFT bin spacing."""
        return (self.adc_sample_rate_ksps * 1e3 * C_LIGHT) / (
            2.0 * self.freq_slope_mhz_us * 1e12
        )

    @property
    def bytes_per_frame(self) -> int:
        """Raw ADC frame size on DCA1000 path: 16-bit I + 16-bit Q per sample."""
        return self.num_adc_samples * self.num_rx * self.num_chirps_per_frame * 4


@dataclass
class ProcessingConfig:
    # Distance below which near-antenna leakage dominates the return and
    # soil/metal become physically indistinguishable -- established
    # empirically for this radar (see NOTES.md, 2026-07-09 session: at 10cm
    # standoff, open air/dry soil/solid steel all read within the same
    # narrow band). The live peak search always skips bins inside this
    # radius, regardless of standoff -- a target detected closer than this
    # cannot be trusted no matter how strong its return looks.
    near_field_boundary_m: float = 0.30
    num_frames_average: int = 30       # frames to average per VWC output
    window: str = "hann"

    def min_search_bin(self, chirp_cfg: ChirpConfig) -> int:
        # Bin 0-1 are always excluded outright even if range resolution is
        # coarse enough that near_field_boundary_m alone wouldn't reach them.
        return max(2, round(self.near_field_boundary_m / chirp_cfg.range_resolution_m))


@dataclass
class CalibrationData:
    """
    Standoff is no longer assumed fixed: the soil-surface bin (and therefore
    the standoff) is detected live, every frame. To keep |Gamma| meaningful
    at whatever distance the sensor happens to be, the metal-plate reference
    is modeled as a function of distance rather than a single scalar:

        metal_amplitude(standoff) = range_coeff_k / standoff ** range_exponent

    range_exponent defaults to 2.0 -- appropriate for a flat, beam-filling
    reflector (soil, a metal plate) acting like a mirror, as opposed to the
    1/R^4 falloff of a small point target. With two or more metal_calibration_points
    at genuinely different standoffs, range_exponent is refit empirically for
    this specific setup instead of relying on the theoretical default.
    """
    metal_calibration_points: List[Tuple[float, float]] = field(default_factory=list)  # (standoff_m, amplitude)
    range_exponent: float = 2.0
    range_coeff_k: Optional[float] = None

    calibration_points: List[Tuple[float, float, float]] = field(default_factory=list)  # (amplitude, vwc, standoff_m)
    poly_coeffs: Optional[List[float]] = None

    def is_ready(self) -> bool:
        return len(self.metal_calibration_points) > 0 and len(self.calibration_points) >= 1

    def metal_amplitude_at(self, standoff_m: float) -> float:
        """Predicted metal-plate (|Gamma|=1) amplitude at any standoff."""
        if self.range_coeff_k is None:
            return 1.0
        return self.range_coeff_k / max(standoff_m, 1e-3) ** self.range_exponent
