"""Coherent integrator — stack N successive range profiles + slow-time FFT.

Per Hysell 2018 §2: "Each range gate is sampled every 100 ms and
coherently processed for 10 s."  We stack 100 successive complex range
profiles (= 10 s at the 100-ms code period) into a slow-time × range
matrix and take the FFT along the slow-time axis to obtain a
range-Doppler matrix.

Doppler resolution = 1 / coherent_seconds (0.1 Hz at the defaults).
Doppler ambiguity = 1 / code_period (10 Hz, ±5 Hz around zero).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class CoherentStack:
    """Accumulator for a single (Tx, freq) coherent window."""
    n_reps: int           # how many range profiles per coherent window
    n_range_bins: int     # range bins per profile

    def __post_init__(self) -> None:
        self._buffer = np.zeros((self.n_reps, self.n_range_bins), dtype=np.complex64)
        self._fill = 0

    def push(self, range_profile: np.ndarray) -> bool:
        """Append one range profile.  Returns True when the stack is full."""
        if range_profile.shape != (self.n_range_bins,):
            raise ValueError(
                f"range_profile shape {range_profile.shape} != "
                f"({self.n_range_bins},)"
            )
        self._buffer[self._fill, :] = range_profile.astype(np.complex64, copy=False)
        self._fill += 1
        return self._fill >= self.n_reps

    def is_full(self) -> bool:
        return self._fill >= self.n_reps

    def reset(self) -> None:
        self._buffer.fill(0)
        self._fill = 0

    def range_doppler(self) -> np.ndarray:
        """Compute the range-Doppler matrix from the accumulated stack.

        FFT is taken along axis 0 (slow-time / Doppler).  Output is
        shape (n_reps, n_range_bins) with Doppler-bin 0 at the start
        (use np.fft.fftshift for centred display).
        """
        if not self.is_full():
            raise RuntimeError(
                f"stack not full ({self._fill}/{self.n_reps}); cannot compute "
                f"range-Doppler"
            )
        return np.fft.fft(self._buffer, axis=0).astype(np.complex64)


def doppler_axis_hz(n_reps: int, code_period_s: float) -> np.ndarray:
    """Compute the Doppler frequency axis for an n_reps×N range-Doppler matrix.

    The axis is returned in FFT order (DC first); apply
    ``np.fft.fftshift`` to centre.
    """
    return np.fft.fftfreq(n_reps, d=code_period_s).astype(np.float32)


def range_axis_km(n_range_bins: int, chip_microseconds: float) -> np.ndarray:
    """Compute the range axis (one-way, km) for an n_range_bins range profile.

    Two-way: pseudorange = range_bin × chip_us × c / 2 / 1e3 (km).
    """
    c = 2.998e8
    return (
        np.arange(n_range_bins, dtype=np.float32)
        * (chip_microseconds * 1e-6)
        * c
        / 2.0
        / 1.0e3
    )


@dataclass
class IncoherentAccumulator:
    """Accumulator for N successive |range-Doppler|² power matrices."""
    n_windows: int

    def __post_init__(self) -> None:
        self._sum: Optional[np.ndarray] = None
        self._fill = 0

    def push(self, range_doppler: np.ndarray) -> bool:
        """Add |range_doppler|² to the running sum.  Returns True when full."""
        power = (np.abs(range_doppler) ** 2).astype(np.float32)
        if self._sum is None:
            self._sum = power
        else:
            self._sum += power
        self._fill += 1
        return self._fill >= self.n_windows

    def average(self) -> np.ndarray:
        """Return the mean power matrix (float32)."""
        if self._sum is None or self._fill == 0:
            raise RuntimeError("no windows accumulated")
        return (self._sum / float(self._fill)).astype(np.float32)

    def reset(self) -> None:
        self._sum = None
        self._fill = 0
