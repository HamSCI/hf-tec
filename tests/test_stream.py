#!/usr/bin/env python3
"""hf-gps-tec stream timing: the frame anchor is derived from the RTP
counter (rtp_to_wallclock + hf-timestd authority offset), NOT the host
wall clock, and each frame's label projects off that anchor by sample
count — the DASI2 RTP-reference invariant (METROLOGY.md §4.5)."""

import queue
import sys
import types
import unittest
from datetime import datetime, timezone

import numpy as np

from hf_gps_tec.core.stream import HfGpsTecSource

_BASE = 1_750_000_000.0       # arbitrary epoch seconds returned by rtp_to_wallclock
_OFFSET_S = 4250 / 1e9        # hf-timestd authority offset (ns -> s)


class _FakeSnap:
    offset_usable = True
    offset_seconds = _OFFSET_S
    t_level_active = "T6"


class _FakeReader:
    def __init__(self, snap):
        self._snap = snap

    def read(self):
        return self._snap


def _install_fake_ka9q(rtp_to_wallclock):
    ka9q = sys.modules.get("ka9q") or types.ModuleType("ka9q")
    sys.modules["ka9q"] = ka9q
    rr = types.ModuleType("ka9q.rtp_recorder")
    rr.rtp_to_wallclock = rtp_to_wallclock
    sys.modules["ka9q.rtp_recorder"] = rr


def _source(sr=100_000, n=10_000):
    return HfGpsTecSource(
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


if __name__ == "__main__":
    unittest.main()
