"""First-hop detector + Doppler 1st-moment + amplitude.

Implements Hysell 2018 §2: "At each time step, the beacon data
analysis algorithm determines the first range gate corresponding to
the first-hop echo. This is interpreted as the pseudorange of the
X-mode signal. The first moment of the Doppler spectrum in the given
range gate is then interpreted as the Doppler shift."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .coherent import doppler_axis_hz, range_axis_km


@dataclass(frozen=True)
class Detection:
    """One first-hop detection from a 1-min incoherent-averaged matrix."""
    pseudorange_km: float
    doppler_hz: float
    amplitude_db: float
    snr_db: float
    noise_floor_db: float
    lock_quality: float
    range_bin: int


def estimate_noise_floor(power_matrix: np.ndarray, percentile: float = 30.0) -> float:
    """Estimate the incoherent noise floor as the Nth percentile of the
    power matrix (excluding the strongest bins).

    A low percentile (30%) captures the long tail of empty-bin power
    that is dominated by thermal + receiver noise, avoiding bias from
    a few strong real returns.
    """
    flat = power_matrix.ravel()
    finite = flat[np.isfinite(flat)]
    if finite.size == 0:
        return 0.0
    return float(np.percentile(finite, percentile))


def first_hop_detection(
    power_matrix: np.ndarray,
    *,
    chip_microseconds: float,
    code_period_s: float,
    snr_threshold_db: float = 8.0,
    min_pseudorange_km: float = 100.0,
    max_pseudorange_km: float = 15000.0,
) -> Optional[Detection]:
    """Find the first range bin with a Doppler peak exceeding the SNR gate.

    ``power_matrix`` shape: (n_doppler, n_range), real, ≥0.

    Returns None if no bin meets the gate within the configured range
    window.
    """
    if power_matrix.ndim != 2:
        raise ValueError(f"power_matrix must be 2D, got shape {power_matrix.shape}")
    n_doppler, n_range = power_matrix.shape

    range_km = range_axis_km(n_range, chip_microseconds)
    doppler_hz = doppler_axis_hz(n_doppler, code_period_s)

    noise = estimate_noise_floor(power_matrix)
    if noise <= 0.0:
        noise = float(np.median(power_matrix)) or 1e-12
    threshold_lin = noise * (10.0 ** (snr_threshold_db / 10.0))

    valid_range = (range_km >= min_pseudorange_km) & (range_km <= max_pseudorange_km)
    valid_bins = np.flatnonzero(valid_range)
    if valid_bins.size == 0:
        return None

    # For each candidate range bin, the figure of merit is the peak power
    # across Doppler.  Walk in order of increasing range and return the
    # first bin that exceeds threshold.
    for bin_idx in valid_bins:
        column = power_matrix[:, bin_idx]
        peak_power = float(column.max())
        if peak_power < threshold_lin:
            continue
        # Found the first-hop.  Compute the Doppler 1st moment over this
        # range bin's Doppler spectrum (incoherent power weighting).
        total = float(column.sum())
        if total <= 0.0:
            continue
        doppler_centroid = float(np.sum(doppler_hz * column) / total)

        amplitude_db = 10.0 * np.log10(max(peak_power, 1e-30))
        noise_db = 10.0 * np.log10(max(noise, 1e-30))
        snr_db = amplitude_db - noise_db

        # Lock quality: how sharp is the peak relative to the rest of
        # this range bin's Doppler spectrum?  1.0 = sharp single tone,
        # 0.0 = flat.
        sorted_power = np.sort(column)[::-1]
        if sorted_power.size > 1 and sorted_power[0] > 0:
            lock = 1.0 - float(sorted_power[1] / sorted_power[0])
        else:
            lock = 1.0
        lock = max(0.0, min(1.0, lock))

        return Detection(
            pseudorange_km=float(range_km[bin_idx]),
            doppler_hz=doppler_centroid,
            amplitude_db=amplitude_db,
            snr_db=snr_db,
            noise_floor_db=noise_db,
            lock_quality=lock,
            range_bin=int(bin_idx),
        )

    return None
