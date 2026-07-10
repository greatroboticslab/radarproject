"""
Volumetric water content estimation.

Two-step conversion:
  1. Normalised reflection amplitude |Γ|  →  apparent dielectric constant εr
     via normal-incidence Fresnel formula (lossless approximation):
         εr = ((1 + |Γ|) / (1 − |Γ|))²

  2. εr → VWC via Topp's equation (Topp et al., 1980):
         θ = −5.3×10⁻² + 2.92×10⁻²·εr − 5.5×10⁻⁴·εr² + 4.3×10⁻⁶·εr³

     Valid range: εr ≈ 1–40, θ ≈ 0–0.5 m³/m³.

Notes on applicability at 60 GHz:
  • Soil at mmWave frequencies has significant dielectric loss (imaginary part).
    Topp's equation was derived at 20–200 MHz (TDR).  Use empirical
    calibration (calibrate.py) to correct for frequency-dependent εr offsets.
  • Rough soil surfaces introduce additional scattering that attenuates the
    specular return.  Flatten the surface or add a surface-roughness correction.
  • The Fresnel model assumes plane-wave normal incidence.  At 3.75 cm range
    resolution and 10–30 cm standoff the sensor is in its near field
    (~R_ff = 2D²/λ ≈ 0.3 m for the AOP aperture), which may add a constant
    bias – compensated by the metal-plate calibration step.
"""

import numpy as np


def gamma_to_permittivity(gamma: float) -> float:
    """
    Convert magnitude of Fresnel reflection coefficient to real permittivity.

    Parameters
    ----------
    gamma : float
        |Γ| in [0, 1).  Values ≥ 1 are physically invalid; clamped to 0.99.

    Returns
    -------
    float
        Apparent relative permittivity εr ≥ 1.
    """
    gamma = float(np.clip(gamma, 0.0, 0.99))
    return ((1.0 + gamma) / (1.0 - gamma)) ** 2


def permittivity_to_vwc(eps_r: float) -> float:
    """
    Topp's equation: volumetric water content from apparent permittivity.

    Parameters
    ----------
    eps_r : float
        Apparent relative permittivity (dimensionless).

    Returns
    -------
    float
        Volumetric water content, m³/m³, clamped to [0, 0.6].
    """
    e = float(eps_r)
    vwc = -5.3e-2 + 2.92e-2 * e - 5.5e-4 * e ** 2 + 4.3e-6 * e ** 3
    return float(np.clip(vwc, 0.0, 0.6))


def gamma_to_vwc(gamma: float) -> tuple[float, float]:
    """
    Convenience wrapper: |Γ| → (εr, VWC).

    Returns
    -------
    (eps_r, vwc) : tuple[float, float]
    """
    eps_r = gamma_to_permittivity(gamma)
    vwc = permittivity_to_vwc(eps_r)
    return eps_r, vwc


def topp_invert(vwc_target: float) -> float:
    """
    Numerically invert Topp's equation to get εr from a known VWC.
    Useful for checking calibration point consistency.
    """
    from scipy.optimize import brentq

    def residual(e):
        return permittivity_to_vwc(e) - vwc_target

    vwc_target = float(np.clip(vwc_target, 0.0, 0.55))
    try:
        return brentq(residual, 1.0, 80.0)
    except ValueError:
        return float("nan")
