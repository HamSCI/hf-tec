#!/usr/bin/env python3
"""The per-pipeline worker must release its source (RadiodStream RX thread
+ sample queue) on EVERY exit path — including a crash — so a
crashed-then-restarted pipeline doesn't orphan its prior RTP subscription."""

import threading
import unittest

from hf_gps_tec.core.daemon import _PipelineWorker


class _FakePipeline:
    def __init__(self, stop_event, raise_on_frames=True):
        self.closed = 0
        self._stop = stop_event
        self._raise = raise_on_frames
        self.source = self                      # frames() lives here

    def frames(self):
        if self._raise:
            raise RuntimeError("boom")
        return iter(())

    def process_frame(self, _frame):            # pragma: no cover
        pass

    def close(self):
        self.closed += 1
        self._stop.set()                        # end the worker after 1st close


class TestPipelineWorkerCleanup(unittest.TestCase):
    def test_close_runs_on_crash_path(self):
        ev = threading.Event()
        fp = _FakePipeline(ev, raise_on_frames=True)
        w = _PipelineWorker(pipeline_factory=lambda: fp, name="t", stop_event=ev)
        w._run()                                # crashes once, closes, ev set -> exits
        self.assertEqual(fp.closed, 1)

    def test_close_runs_on_clean_exhaustion(self):
        ev = threading.Event()
        fp = _FakePipeline(ev, raise_on_frames=False)
        w = _PipelineWorker(pipeline_factory=lambda: fp, name="t", stop_event=ev)
        w._run()                                # source exhausts cleanly, closes, returns
        self.assertEqual(fp.closed, 1)


if __name__ == "__main__":
    unittest.main()
