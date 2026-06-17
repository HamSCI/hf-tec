"""Code-free PRN-beacon detector.

Detects the *presence* of a PRN-coded HF beacon at a known carrier
frequency without knowing the actual PRN sequence — by exploiting the
fact that any PRN beacon of the family described in Hysell et al.
(2018) §2 repeats its waveform every 100 ms.

The principal statistic is the lagged autocorrelation at τ = one code
period (default 100 ms):

    r(τ) = ⟨ s(t) · s*(t + τ) ⟩  /  ⟨ |s(t)|² ⟩

For Gaussian noise alone, r(τ) ≈ 0 (with variance ∝ 1/N where N is
the sample count).  For a periodic-at-τ signal with power P_s in the
presence of noise power P_n,

    |r(τ)| ≈ P_s / (P_s + P_n)
    arg r(τ) = −2π · f_d · τ   →   Doppler shift f_d

So a single complex number both confirms detection and yields the
Doppler shift, without any code knowledge.

A reference autocorrelation at a non-code-period lag (default 137 ms,
chosen to be coprime with the code period) gives the noise floor for
|r|.  The detection statistic is the dB ratio of the two magnitudes.

What this detector CANNOT do:
  - Distinguish co-band transmitters (all PRN codes share the code
    period).  When multiple transmitters are on-air on the same
    frequency, |r| sums their contributions.
  - Measure absolute pseudorange (no code-phase reference).

What it CAN do at this scaffolding stage:
  - Confirm whether the antenna chain + radiod is receiving the
    transmitter at all (first-light test).
  - Produce a diurnal propagation time series — band power and
    detection ratio vs UTC — usable to characterise when a signal
    is reaching the receiver.
  - Recover Doppler shift (full ±5 Hz unambiguous span at the
    100-ms code period).

References:
  Hysell et al. (2018) §2 — PRN waveform and 100-ms code repetition.
  docs/RECEIVER.md §6 — gap list (this detector closes Gap 1 in a
  weakened form: detect but not decode).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


# Default reference-lag offset.  Chosen so the reference lag is not a
# small-integer multiple of the code period and is unlikely to fall on
# any other periodicity an HF receiver might see.  In samples at the
# nominal 100 kS/s rate this is 13,700, vs the 10,000-sample code lag.
DEFAULT_REFERENCE_LAG_OFFSET_S = 0.037


@dataclass(frozen=True)
class CodelessDetection:
    """One per-minute codeless detection record."""
    integration_seconds: float
    n_samples: int

    # Lagged autocorrelations.
    autocorr_magnitude: float        # |r(τ=code_period)|, range [0, 1]
    autocorr_floor: float            # |r(τ=ref_lag)|, expected ≈ 1/√N for noise
    autocorr_db: float               # 20·log10(magnitude / floor)
    autocorr_phase_rad: float        # arg r(τ=code_period), unwrapped to (−π, π]

    # Derived observables.
    doppler_hz: float                # = −phase / (2π · code_period_s)
    snr_estimate_db: float           # = 10·log10(|r| / (1 − |r|)), ≈ P_s / P_n
    band_power_db: float             # 10·log10(mean |iq|²) — uncalibrated

    # Detection outcome.
    detection: bool                  # True when autocorr_db ≥ threshold
    detection_threshold_db: float


def lagged_autocorr(iq: np.ndarray, lag_samples: int) -> complex:
    """Normalised lagged autocorrelation at one specific lag.

    Returns a complex number whose magnitude is roughly the signal-power
    fraction of the total received power, and whose phase is −2π f_d τ
    (Doppler shift times the lag).
    """
    if lag_samples <= 0 or lag_samples >= iq.size:
        raise ValueError(f"lag_samples {lag_samples} out of bounds for buffer size {iq.size}")
    if iq.dtype != np.complex64 and iq.dtype != np.complex128:
        iq = iq.astype(np.complex64, copy=False)
    s1 = iq[: iq.size - lag_samples]
    s2 = iq[lag_samples:]
    num = np.sum(s1 * np.conj(s2))
    den = np.sum(np.abs(iq).astype(np.float64) ** 2)
    if den <= 0:
        return 0.0 + 0.0j
    return complex(num / den)


def codeless_detect(
    iq: np.ndarray,
    *,
    sample_rate_hz: int,
    code_period_s: float = 0.1,
    reference_lag_offset_s: float = DEFAULT_REFERENCE_LAG_OFFSET_S,
    detection_threshold_db: float = 6.0,
) -> Optional[CodelessDetection]:
    """Run the full code-free detection statistic on one integration window.

    Returns a populated CodelessDetection (with ``detection`` set
    according to the configured threshold), or None if the buffer is
    too short to support the configured lags.
    """
    code_lag = int(round(code_period_s * sample_rate_hz))
    ref_lag = code_lag + int(round(reference_lag_offset_s * sample_rate_hz))
    if iq.size < ref_lag + 1:
        return None

    r_code = lagged_autocorr(iq, code_lag)
    r_ref = lagged_autocorr(iq, ref_lag)

    autocorr_magnitude = float(abs(r_code))
    autocorr_floor = float(abs(r_ref))
    if autocorr_floor <= 0:
        # Pathological: floor of zero means we can't form a ratio.
        # Treat as no detection.
        autocorr_db = 0.0
    else:
        autocorr_db = 20.0 * np.log10(
            max(autocorr_magnitude, 1e-30) / max(autocorr_floor, 1e-30)
        )

    code_period_actual_s = code_lag / sample_rate_hz
    phase = float(np.angle(r_code))
    doppler_hz = -phase / (2.0 * np.pi * code_period_actual_s)

    if 0.0 < autocorr_magnitude < 1.0:
        snr_estimate_db = 10.0 * np.log10(
            autocorr_magnitude / (1.0 - autocorr_magnitude)
        )
    elif autocorr_magnitude >= 1.0:
        # Effectively noise-free input or numerical edge.
        snr_estimate_db = 60.0
    else:
        snr_estimate_db = -float("inf")

    mean_power = float(np.mean(np.abs(iq).astype(np.float64) ** 2))
    band_power_db = 10.0 * np.log10(max(mean_power, 1e-30))

    detection = bool(autocorr_db >= detection_threshold_db)

    return CodelessDetection(
        integration_seconds=iq.size / float(sample_rate_hz),
        n_samples=int(iq.size),
        autocorr_magnitude=float(autocorr_magnitude),
        autocorr_floor=float(autocorr_floor),
        autocorr_db=float(autocorr_db),
        autocorr_phase_rad=float(phase),
        doppler_hz=float(doppler_hz),
        snr_estimate_db=float(snr_estimate_db),
        band_power_db=float(band_power_db),
        detection=detection,
        detection_threshold_db=float(detection_threshold_db),
    )
