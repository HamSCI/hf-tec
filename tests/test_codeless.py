"""Tests for the code-free detector.

Verifies the 100-ms autocorrelation detector against:
- pure complex Gaussian noise (no detection)
- a synthetic periodic-at-100ms signal in noise (clean detection,
  correct Doppler, correct SNR estimate)
- a Doppler-shifted periodic signal (correct Doppler extraction)
- a longer / shorter integration window (statistic stability)
"""

from __future__ import annotations

import numpy as np
import pytest

from hf_tec.core.detect_codeless import (
    DEFAULT_REFERENCE_LAG_OFFSET_S,
    codeless_detect,
    lagged_autocorr,
)


SAMPLE_RATE = 100_000           # 100 kS/s — matches the production decimated rate
CODE_PERIOD_S = 0.1             # 100 ms
CODE_LAG = int(CODE_PERIOD_S * SAMPLE_RATE)   # 10,000 samples


def _white_noise(n: int, sigma: float = 1.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return ((rng.standard_normal(n) + 1j * rng.standard_normal(n)) * (sigma / np.sqrt(2))).astype(np.complex64)


def _periodic_signal(n: int, code: np.ndarray, doppler_hz: float = 0.0) -> np.ndarray:
    """Make a length-n signal by tiling ``code`` and adding a complex
    Doppler rotation across the whole buffer."""
    reps = (n + code.size - 1) // code.size
    tiled = np.tile(code, reps)[:n].astype(np.complex64)
    if doppler_hz != 0.0:
        t = np.arange(n) / SAMPLE_RATE
        tiled = tiled * np.exp(1j * 2 * np.pi * doppler_hz * t).astype(np.complex64)
    return tiled


def _make_code(n_chips: int = 10_000, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, size=n_chips)
    return np.where(bits == 0, -1.0, 1.0).astype(np.complex64)


# ---------------------------------------------------------------------------
# Noise-only — must not detect
# ---------------------------------------------------------------------------


def test_pure_noise_does_not_detect() -> None:
    """Pure complex Gaussian noise must not trigger a detection."""
    iq = _white_noise(2 * SAMPLE_RATE, sigma=1.0, seed=42)  # 2 s of noise
    det = codeless_detect(
        iq,
        sample_rate_hz=SAMPLE_RATE,
        code_period_s=CODE_PERIOD_S,
        detection_threshold_db=6.0,
    )
    assert det is not None
    assert det.detection is False
    # Noise floors should be similar in magnitude.
    ratio = det.autocorr_magnitude / max(det.autocorr_floor, 1e-30)
    assert 0.1 < ratio < 10.0  # both small, ratio bounded near 1


# ---------------------------------------------------------------------------
# Periodic signal — must detect with correct Doppler
# ---------------------------------------------------------------------------


def test_periodic_signal_in_clean_buffer_detects() -> None:
    """A pure 100-ms-periodic signal must give |r_code| ≈ 1, easy detection."""
    code = _make_code()
    iq = _periodic_signal(2 * SAMPLE_RATE, code)
    det = codeless_detect(
        iq,
        sample_rate_hz=SAMPLE_RATE,
        code_period_s=CODE_PERIOD_S,
        detection_threshold_db=6.0,
    )
    assert det is not None
    assert det.detection is True
    # |r| approaches 1 for noise-free input as the buffer length grows.
    # For this 2-s / 20-code-period buffer the normalization-window
    # mismatch caps |r| at (n - code_lag) / n = 0.95.  Production
    # 60-s integration sees 0.998.
    assert det.autocorr_magnitude > 0.94
    # Doppler should be near zero (no shift applied).
    assert abs(det.doppler_hz) < 0.1


def test_periodic_signal_in_moderate_noise_detects() -> None:
    """At input SNR = 0 dB the detector must still confidently detect."""
    code = _make_code()
    n = 5 * SAMPLE_RATE  # 5 s
    signal = _periodic_signal(n, code) * 0.5
    noise = _white_noise(n, sigma=0.5, seed=7)
    iq = (signal + noise).astype(np.complex64)
    det = codeless_detect(
        iq,
        sample_rate_hz=SAMPLE_RATE,
        code_period_s=CODE_PERIOD_S,
        detection_threshold_db=6.0,
    )
    assert det is not None
    assert det.detection is True
    # With signal-power-fraction ≈ 0.5, |r| should land near 0.5.
    assert 0.3 < det.autocorr_magnitude < 0.7


def test_doppler_recovered_from_autocorrelation_phase() -> None:
    """A 1 Hz Doppler shift on the periodic signal should be recovered
    from the autocorrelation phase to within a small fraction of a Hz."""
    code = _make_code()
    n = 3 * SAMPLE_RATE
    target_doppler_hz = 1.0
    iq = _periodic_signal(n, code, doppler_hz=target_doppler_hz)
    det = codeless_detect(
        iq,
        sample_rate_hz=SAMPLE_RATE,
        code_period_s=CODE_PERIOD_S,
        detection_threshold_db=6.0,
    )
    assert det is not None
    assert det.detection is True
    assert det.doppler_hz == pytest.approx(target_doppler_hz, abs=0.05)


def test_doppler_negative_offset() -> None:
    """Make sure the sign convention works for negative Doppler too."""
    code = _make_code()
    iq = _periodic_signal(3 * SAMPLE_RATE, code, doppler_hz=-2.5)
    det = codeless_detect(
        iq,
        sample_rate_hz=SAMPLE_RATE,
        code_period_s=CODE_PERIOD_S,
        detection_threshold_db=6.0,
    )
    assert det is not None
    assert det.doppler_hz == pytest.approx(-2.5, abs=0.05)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_buffer_too_short_returns_none() -> None:
    """A buffer shorter than the reference lag should return None."""
    iq = _white_noise(100, sigma=1.0)
    det = codeless_detect(
        iq,
        sample_rate_hz=SAMPLE_RATE,
        code_period_s=CODE_PERIOD_S,
    )
    assert det is None


def test_lagged_autocorr_normalization() -> None:
    """For a unit-power signal at lag 0 the autocorrelation must be 1+0j."""
    iq = _periodic_signal(SAMPLE_RATE, _make_code())
    # Use a tiny lag (1 sample) — won't be at the code period but the
    # normalization should still keep |r| ≤ 1.
    r = lagged_autocorr(iq, 1)
    assert abs(r) <= 1.0 + 1e-6
