"""PRN replica generation + FFT-based cross-correlation.

The PRN code generator is a direct port of Dr. David Hysell's
``create_pseudo_random_code`` routine (received from AC0G,
2026-05-29).  Each code is a length-``clen`` array of unit-magnitude
complex phases drawn uniformly from [0, 2π); the per-station integer
seed selects the sequence.

Per-station seed mapping (Hysell, 2026-05-29):

    seed 0 → Poker Flat, Alaska
    seed 1 → Gakona, Alaska
    seed 2 → Palmer, Alaska (currently down for maintenance)

Cornell University is planned as a second-region transmitter; its
seed will be assigned when that Tx comes on-air.

Network parameters (Hysell, 2026-05-29):

  Currently   : clen=10_000 chips × 10 µs  → 100-ms code period, 100 kHz BW
  Planned     : clen= 5_000 chips × 20 µs  → 100-ms code period,  50 kHz BW

Both regimes keep the 100-ms UTC-aligned code period.  Carriers are
2.9 MHz and 3.4 MHz, emitted simultaneously from each Tx.

References:
  - Hysell et al. (2018, *JGR Space Physics* 123:6851–6864), §2.
  - docs/RECEIVER.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np


# ---------------------------------------------------------------------------
# PRN code generator — Hysell algorithm
# ---------------------------------------------------------------------------

#: Real generator is wired in.  Kept as a module-level flag because the
#: contract surface (`contract.build_inventory`) reads it to decide
#: whether to warn operators about codeless-only operation.
PRN_IS_STUB: bool = False


def generate_prn_code(
    prn_seed: int,
    n_chips: int = 10_000,
) -> np.ndarray:
    """Generate one Hysell per-station PRN phase code.

    Direct port of ``create_pseudo_random_code(clen, seed)``:

        rng    = legacy numpy Mersenne-Twister, seeded with ``prn_seed``
        phases = exp(1j · 2π · rng.random_sample(n_chips))

    The code is complex (continuous-phase PSK on the unit circle),
    not real BPSK.  Exact bit-for-bit reproduction of Hysell's
    reference output requires ``numpy.random.RandomState`` (legacy
    Mersenne Twister); do NOT switch to ``default_rng`` (PCG64) — it
    would produce a different sequence even for the same seed and
    silently desync from the transmitter.
    """
    rng = np.random.RandomState(int(prn_seed))
    phases = np.exp(1j * 2.0 * np.pi * rng.random_sample(int(n_chips)))
    return phases.astype(np.complex64)


# ---------------------------------------------------------------------------
# Replica bank
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Replica:
    """One precomputed transmitter replica for FFT-based correlation."""
    tx_id: str
    frequency_hz: int
    prn_seed: int
    chips: np.ndarray                # complex64, |chip|=1, shape (n_chips,)
    fft_conj: np.ndarray             # complex64, shape (n_samples,)


class ReplicaBank:
    """Bank of precomputed replicas for one receive frequency.

    The replicas are upsampled (chip-rate → sample-rate) and their
    conjugate FFTs are cached.  Correlation against a received frame
    is then a single multiply + IFFT per replica.
    """

    def __init__(self, n_samples: int, samples_per_chip: int):
        if n_samples % samples_per_chip != 0:
            raise ValueError(
                f"n_samples ({n_samples}) must be a multiple of "
                f"samples_per_chip ({samples_per_chip})"
            )
        self.n_samples = int(n_samples)
        self.samples_per_chip = int(samples_per_chip)
        self.n_chips = self.n_samples // self.samples_per_chip
        self._replicas: dict[tuple[str, int], Replica] = {}

    def add(
        self,
        tx_id: str,
        frequency_hz: int,
        *,
        prn_seed: int,
    ) -> Replica:
        key = (tx_id.upper(), int(frequency_hz))
        if key in self._replicas:
            return self._replicas[key]
        chips = generate_prn_code(prn_seed, n_chips=self.n_chips)
        # Upsample by sample-and-hold across `samples_per_chip` samples.
        # Hysell's rep_seq() copies each chip `rep` times — identical
        # operation, just expressed via np.repeat.
        upsampled = np.repeat(chips, self.samples_per_chip).astype(np.complex64)
        # Precompute conj(FFT(replica)) for FFT-based circular correlation.
        replica = Replica(
            tx_id=key[0],
            frequency_hz=key[1],
            prn_seed=int(prn_seed),
            chips=chips,
            fft_conj=np.conj(np.fft.fft(upsampled)).astype(np.complex64),
        )
        self._replicas[key] = replica
        return replica

    def add_many(
        self,
        tx_seeds: Iterable[tuple[str, int]],
        frequency_hz: int,
    ) -> list[Replica]:
        return [self.add(t, frequency_hz, prn_seed=s) for t, s in tx_seeds]

    def __iter__(self):
        return iter(self._replicas.values())

    def __len__(self) -> int:
        return len(self._replicas)


# ---------------------------------------------------------------------------
# Correlator
# ---------------------------------------------------------------------------


def correlate(rx_frame: np.ndarray, replica: Replica) -> np.ndarray:
    """FFT-based circular cross-correlation of one rx frame with one replica.

    rx_frame : complex64, shape (n_samples,)
    replica  : Replica with fft_conj precomputed.

    Returns a complex64 range profile of shape (n_chips,) — one bin per
    chip (i.e. one bin per range-resolution cell).
    """
    if rx_frame.dtype != np.complex64:
        rx_frame = rx_frame.astype(np.complex64, copy=False)
    if rx_frame.shape[0] != replica.fft_conj.shape[0]:
        raise ValueError(
            f"rx_frame length {rx_frame.shape[0]} != replica length "
            f"{replica.fft_conj.shape[0]}"
        )
    spectrum = np.fft.fft(rx_frame)
    corr_fft = spectrum * replica.fft_conj
    corr = np.fft.ifft(corr_fft).astype(np.complex64)
    # Decimate by samples_per_chip to get one bin per range cell.
    samples_per_chip = corr.shape[0] // replica.chips.shape[0]
    if samples_per_chip == 1:
        return corr
    return corr[::samples_per_chip].copy()


def correlate_bank(rx_frame: np.ndarray, bank: ReplicaBank) -> dict[str, np.ndarray]:
    """Correlate one rx frame against every replica in the bank.

    Returns a dict mapping tx_id → complex range profile.
    """
    spectrum = np.fft.fft(rx_frame.astype(np.complex64, copy=False))
    samples_per_chip = bank.samples_per_chip
    out: dict[str, np.ndarray] = {}
    for rep in bank:
        corr = np.fft.ifft(spectrum * rep.fft_conj).astype(np.complex64)
        if samples_per_chip > 1:
            corr = corr[::samples_per_chip].copy()
        out[rep.tx_id] = corr
    return out
