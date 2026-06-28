#!/usr/bin/env python3
"""hf-tec stream timing: the frame anchor is derived from the RTP
counter (ka9q.rtp_to_utc + hf-timestd authority offset, via the shared
hamsci_dsp.timing.acquire_anchor_utc helper), NOT the host wall clock,
and each frame's label projects off that anchor by sample count — the
DASI2 RTP-reference invariant (METROLOGY.md §4.5)."""

import queue
import sys
import types
import unittest
from datetime import datetime, timezone

import numpy as np

from hf_tec.core.stream import HfTecSource

_BASE = 1_750_000_000.0       # arbitrary epoch seconds returned by rtp_to_utc
_OFFSET_S = 4250 / 1e9        # hf-timestd authority offset (ns -> s)


class _FakeSnap:
    offset_usable = True
    offset_seconds = _OFFSET_S
    rtp_to_utc_offset_ns = 4250
    t_level_active = "T6"


class _FakeReader:
    def __init__(self, snap):
        self._snap = snap

    def read(self):
        return self._snap


def _install_fake_ka9q(rtp_to_utc):
    ka9q = sys.modules.get("ka9q") or types.ModuleType("ka9q")
    ka9q.rtp_to_utc = rtp_to_utc
    sys.modules["ka9q"] = ka9q
    # rtp_to_wallclock is the deprecated alias kept on the rtp_recorder module.
    rr = types.ModuleType("ka9q.rtp_recorder")
    rr.rtp_to_wallclock = rtp_to_utc
    sys.modules["ka9q.rtp_recorder"] = rr


def _source(sr=100_000, n=10_000):
    return HfTecSource(
        radiod_status="x", frequency_hz=2_900_000,
        sample_rate_hz=sr, frame_n_samples=n,
    )


class TestRtpDerivedAnchor(unittest.TestCase):
    def test_anchor_is_rtp_derived_plus_authority_offset(self):
        seen = {}

        def fake(rtp, channel_info, wallclock_hint_sec=None):
            seen["rtp"] = rtp
            return _BASE

        _install_fake_ka9q(fake)
        src = _source()
        src._anchor_first_rtp = 123456
        src._channel_info = object()
        src._authority_reader = _FakeReader(_FakeSnap())
        anchor = src._compute_anchor_utc()
        self.assertEqual(
            anchor,
            datetime.fromtimestamp(_BASE + _OFFSET_S, tz=timezone.utc),
        )
        self.assertEqual(seen["rtp"], 123456)  # the captured first RTP ts

    def test_fallback_to_wallclock_when_rtp_unavailable(self):
        _install_fake_ka9q(lambda *a, **k: None)
        src = _source()
        src._anchor_first_rtp = None        # never captured
        src._channel_info = None
        src._authority_reader = _FakeReader(None)
        before = datetime.now(timezone.utc)
        anchor = src._compute_anchor_utc()
        after = datetime.now(timezone.utc)
        self.assertTrue(before <= anchor <= after)

    def test_frames_label_by_sample_count_not_wall_clock(self):
        _install_fake_ka9q(
            lambda rtp, ci, wallclock_hint_sec=None: _BASE
        )
        sr, n = 100_000, 10_000
        src = _source(sr, n)
        src._stream = object()                       # skip open()
        src._sample_queue = queue.Queue()
        src._anchor_first_rtp = 1
        src._channel_info = object()
        src._authority_reader = _FakeReader(_FakeSnap())
        # Feed exactly two frames' worth of samples.
        src._sample_queue.put(np.zeros(n, dtype=np.complex64))
        src._sample_queue.put(np.zeros(n, dtype=np.complex64))
        gen = src.frames()
        f0 = next(gen)
        f1 = next(gen)
        src._stopped.set()
        # First frame == the RTP-derived anchor (offset applied).
        self.assertEqual(
            f0.timestamp_utc,
            datetime.fromtimestamp(_BASE + _OFFSET_S, tz=timezone.utc),
        )
        # Second frame is exactly one frame-period later (sample-count
        # projection), independent of wall-clock time spent in the loop.
        self.assertAlmostEqual(
            (f1.timestamp_utc - f0.timestamp_utc).total_seconds(),
            n / sr,                                   # 0.1 s
            places=9,
        )
        self.assertEqual(f0.rtp_anchor_ns, 1)


class TestDropAccounting(unittest.TestCase):
    def test_dropped_samples_advance_the_label_over_the_gap(self):
        _install_fake_ka9q(lambda rtp, ci, wallclock_hint_sec=None: _BASE)
        sr, n = 100_000, 10_000
        src = _source(sr, n)
        src._stream = object()
        src._sample_queue = queue.Queue()
        src._anchor_first_rtp = 1
        src._channel_info = object()
        src._authority_reader = _FakeReader(_FakeSnap())

        src._sample_queue.put(np.zeros(n, dtype=np.complex64))
        gen = src.frames()
        f0 = next(gen)
        self.assertEqual(f0.dropped_samples_before, 0)

        # Simulate a queue-overflow drop of one whole frame between f0 and f1.
        src._dropped_samples = n
        src._sample_queue.put(np.zeros(n, dtype=np.complex64))
        f1 = next(gen)
        src._stopped.set()

        # f1 is the 2nd framed block (index 1) PLUS the dropped frame's worth
        # of real time — i.e. two frame-periods after the anchor, not one.
        self.assertEqual(f1.dropped_samples_before, n)
        self.assertAlmostEqual(
            (f1.timestamp_utc - f0.timestamp_utc).total_seconds(),
            2 * n / sr,                               # 0.2 s, gap included
            places=9,
        )


class _EmptyQueue:
    """A queue whose get() always reports empty (no blocking) — lets the
    stall watchdog be exercised without waiting on real 1 s get timeouts."""

    def get(self, timeout=None):
        raise queue.Empty


class TestStallWatchdog(unittest.TestCase):
    def test_frames_raises_when_no_iq_arrives(self):
        from hf_tec.core.stream import SourceStalled

        src = _source()
        src._stream = object()                 # skip open()
        src._sample_queue = _EmptyQueue()
        src.stall_timeout_s = 0.2              # trip quickly
        with self.assertRaises(SourceStalled):
            next(src.frames())

    def test_stall_watchdog_disabled_when_zero(self):
        import threading

        src = _source()
        src._stream = object()
        src._sample_queue = _EmptyQueue()
        src.stall_timeout_s = 0.0              # disabled
        gen = src.frames()
        # With the watchdog off the loop just spins on empties; stop it after
        # a beat and confirm it ends cleanly without raising SourceStalled.
        threading.Timer(0.3, src._stopped.set).start()
        self.assertIsNone(next(gen, None))


if __name__ == "__main__":
    unittest.main()
