"""First-hop detector tests.

Synthesise a range-Doppler power matrix with a known signal injected
into one range bin at a known Doppler offset; verify the detector
returns the right bin + Doppler value.
"""

from __future__ import annotations

import numpy as np
import pytest

from hf_tec.core import detect


def _make_power_matrix(
    n_doppler: int,
    n_range: int,
    signal_bin: int,
    signal_doppler_bin: int,
    signal_power: float = 1000.0,
    noise_level: float = 1.0,
    rng_seed: int = 42,
) -> np.ndarray:
    rng = np.random.default_rng(rng_seed)
    # Exponentially-distributed noise — matches incoherent power statistics
    # for complex Gaussian noise.
    matrix = rng.exponential(scale=noise_level, size=(n_doppler, n_range)).astype(np.float32)
    matrix[signal_doppler_bin, signal_bin] += signal_power
    return matrix


def test_first_hop_returns_first_above_threshold() -> None:
    matrix = _make_power_matrix(
        n_doppler=100, n_range=1000,
        signal_bin=200, signal_doppler_bin=50,
        signal_power=1e6,   # >> any noise spike at this SNR gate
    )
    det = detect.first_hop_detection(
        matrix,
        chip_microseconds=10.0,
        code_period_s=0.1,
        snr_threshold_db=40.0,
        min_pseudorange_km=100.0,
        max_pseudorange_km=15_000.0,
    )
    assert det is not None
    assert det.range_bin == 200
    # chip_us=10 → c * 10e-6 / 2 = 1.499 km/bin (precise c, not the
    # rounded 1.5 km figure quoted in Hysell 2018).  Float32 axis introduces
    # ~1e-4 relative drift — approx is the right comparison.
    assert det.pseudorange_km == pytest.approx(200 * 1.499, rel=1e-4)
    assert det.snr_db > 40.0


def test_returns_none_when_below_threshold() -> None:
    matrix = _make_power_matrix(
        n_doppler=100, n_range=1000,
        signal_bin=200, signal_doppler_bin=50,
        signal_power=0.1,  # below noise
    )
    det = detect.first_hop_detection(
        matrix,
        chip_microseconds=10.0,
        code_period_s=0.1,
        snr_threshold_db=30.0,
    )
    assert det is None


def test_range_window_skips_short_returns() -> None:
    """A peak inside min_pseudorange_km must be ignored."""
    matrix = _make_power_matrix(
        n_doppler=100, n_range=1000,
        signal_bin=10, signal_doppler_bin=50,  # 10 × 1.5 km = 15 km
    )
    det = detect.first_hop_detection(
        matrix,
        chip_microseconds=10.0,
        code_period_s=0.1,
        snr_threshold_db=10.0,
        min_pseudorange_km=100.0,  # excludes bin 10
    )
    assert det is None or det.range_bin > 10


def test_doppler_first_moment_recovers_known_offset() -> None:
    """A pure-tone peak at one Doppler bin should give a 1st moment within
    a couple of bin widths of that bin.

    Uses an SNR gate well above the per-bin exponential-noise tail so the
    detector locks onto the injected peak rather than a random spike,
    and a generous signal-to-noise so the noise contribution to the
    Doppler 1st moment stays small.
    """
    n_doppler, n_range = 100, 500
    target_doppler_bin = 70
    matrix = _make_power_matrix(
        n_doppler=n_doppler, n_range=n_range,
        signal_bin=300, signal_doppler_bin=target_doppler_bin,
        signal_power=1e6, noise_level=0.5,
    )
    det = detect.first_hop_detection(
        matrix,
        chip_microseconds=10.0,
        code_period_s=0.1,
        snr_threshold_db=40.0,
        min_pseudorange_km=100.0,
    )
    assert det is not None
    assert det.range_bin == 300
    expected_hz = np.fft.fftfreq(n_doppler, d=0.1)[target_doppler_bin]
    # 1st moment is biased by noise across the Doppler column.  With this
    # SNR the noise contribution stays under one Doppler bin (0.1 Hz);
    # allow 0.5 Hz headroom for the exponential tail.
    assert abs(det.doppler_hz - expected_hz) < 0.5
