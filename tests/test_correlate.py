"""Correlator tests.

Exercises the FFT-based circular cross-correlation against synthetic
signals built from Hysell's per-station PRN phase code generator.
"""

from __future__ import annotations

import math

import numpy as np

from hf_tec.core import correlate as cc


def test_replica_bank_dimensions() -> None:
    bank = cc.ReplicaBank(n_samples=10_000, samples_per_chip=1)
    rep = bank.add("POKER_FLAT", 2_900_000, prn_seed=0)
    assert rep.chips.shape == (10_000,)
    assert rep.fft_conj.shape == (10_000,)
    assert rep.chips.dtype == np.complex64
    # Every chip must lie on the unit circle (Hysell phase code).
    assert np.allclose(np.abs(rep.chips), 1.0, atol=1e-6)


def test_codes_are_distinct_per_seed() -> None:
    """Different seeds must produce different codes — otherwise the
    correlator would lump distinct Tx together."""
    a = cc.generate_prn_code(prn_seed=0)
    b = cc.generate_prn_code(prn_seed=1)
    c = cc.generate_prn_code(prn_seed=2)
    assert a.shape == b.shape == c.shape == (10_000,)
    assert not np.array_equal(a, b)
    assert not np.array_equal(a, c)
    assert not np.array_equal(b, c)


def test_generator_matches_hysell_reference() -> None:
    """Regression: bit-for-bit match against Hysell's reference algorithm.

    Hysell's create_pseudo_random_code uses legacy numpy RandomState
    (Mersenne Twister); reseeding with the same integer must reproduce
    the exact same complex phase sequence.  This guards against an
    accidental switch to ``np.random.default_rng`` (PCG64), which would
    silently desync from the transmitter.
    """
    rng = np.random.RandomState(0)
    expected = np.exp(1j * 2.0 * math.pi * rng.random_sample(10_000)).astype(np.complex64)
    got = cc.generate_prn_code(prn_seed=0, n_chips=10_000)
    np.testing.assert_array_equal(got, expected)


def test_generator_supports_planned_5000_chip_codes() -> None:
    """Hysell's planned migration is clen=5000 / 20-µs chips / 50 kHz BW.
    The generator must work at the new length without changes."""
    code = cc.generate_prn_code(prn_seed=1, n_chips=5_000)
    assert code.shape == (5_000,)
    assert code.dtype == np.complex64
    assert np.allclose(np.abs(code), 1.0, atol=1e-6)


def test_correlator_peak_at_zero_lag() -> None:
    """A noise-free copy of the replica must produce a sharp peak at lag 0."""
    n_samples = 10_000
    bank = cc.ReplicaBank(n_samples=n_samples, samples_per_chip=1)
    rep = bank.add("POKER_FLAT", 2_900_000, prn_seed=0)
    tx = rep.chips.astype(np.complex64)
    profile = cc.correlate(tx, rep)
    assert profile.shape == (10_000,)
    peak_bin = int(np.argmax(np.abs(profile)))
    assert peak_bin == 0, f"expected peak at lag 0, got bin {peak_bin}"
    peak = np.abs(profile[0])
    median_sidelobe = float(np.median(np.abs(profile[1:])))
    assert peak / max(median_sidelobe, 1e-12) > 50.0


def test_correlator_resolves_delay() -> None:
    """A circularly-shifted replica must produce a peak at the shift bin."""
    n_samples = 10_000
    bank = cc.ReplicaBank(n_samples=n_samples, samples_per_chip=1)
    rep = bank.add("POKER_FLAT", 2_900_000, prn_seed=0)
    shift = 137
    tx = np.roll(rep.chips.astype(np.complex64), shift)
    profile = cc.correlate(tx, rep)
    peak_bin = int(np.argmax(np.abs(profile)))
    assert peak_bin == shift


def test_correlate_bank_keys_by_tx_id() -> None:
    bank = cc.ReplicaBank(n_samples=10_000, samples_per_chip=1)
    bank.add("POKER_FLAT", 2_900_000, prn_seed=0)
    bank.add("GAKONA", 2_900_000, prn_seed=1)
    rx = np.zeros(10_000, dtype=np.complex64)
    profiles = cc.correlate_bank(rx, bank)
    assert set(profiles.keys()) == {"POKER_FLAT", "GAKONA"}
    for prof in profiles.values():
        assert prof.shape == (10_000,)


def test_prn_is_stub_flag_unset() -> None:
    """Hysell's real generator is wired in — the stub flag must be False
    so the contract surface stops emitting the codeless-mode warning."""
    assert cc.PRN_IS_STUB is False
