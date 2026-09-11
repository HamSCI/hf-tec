#!/usr/bin/env python3
"""The §3 ``timing_authority_applied`` report for one hf-tec instance.

The daemon owns one pipeline worker per frequency.  Each worker's source
pins one :class:`hamsci_dsp.timing.AnchorUTC`.  The instance's labels ride
the authority only when every anchored source's do (CLIENT-CONTRACT §18.7:
a mixed state must stay visible, never averaged away).  The daemon leaves
the aggregate at ``<data_root>/<instance>/timing-authority.json`` once a
minute; ``hf-tec inventory --json`` reads it back from the same path.
"""

import dataclasses
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from hamsci_dsp.timing import AnchorUTC, read_applied_state

from hf_tec import config as cfgmod
from hf_tec import contract
from hf_tec.core.applied_state import (
    applied_state_for,
    applied_state_path,
    instance_name,
)
from hf_tec.core.daemon import HfTecRecorder, _PipelineWorker


REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg(reporter_id=None, status_address=""):
    cfg = cfgmod.load_config(REPO_ROOT / "config" / "hf-tec-config.toml.template")
    return dataclasses.replace(
        cfg,
        instance=dataclasses.replace(cfg.instance, reporter_id=reporter_id),
        ka9q=dataclasses.replace(cfg.ka9q, status_address=status_address),
    )


def _anchor(offset_ns, utc=1_700_000_500.0, source="rtp_to_utc+authority"):
    snap = SimpleNamespace(
        t_level_active="T6", sigma_ns=4210, governor_radiod="gov",
        utc_published=None, host_clock=None,
    ) if offset_ns is not None else None
    return AnchorUTC(
        utc=utc, source=source,
        offset_seconds=(offset_ns or 0) / 1e9, offset_ns=offset_ns,
        snapshot=snap, rtp_referenced=True,
    )


def _worker(anchor, running=True):
    """A pipeline worker stand-in.  ``running=False`` models a worker whose
    pipeline crashed and now waits out its backoff: no pipeline, no anchor."""
    pipeline = SimpleNamespace(source=SimpleNamespace(anchor=anchor)) if running else None
    return SimpleNamespace(pipeline=pipeline)


class AppliedStateForTests(unittest.TestCase):

    def test_all_anchored_sources_corrected_reports_populated(self):
        workers = [_worker(_anchor(4_250_000)), _worker(_anchor(4_250_000))]
        block = applied_state_for(workers, client_radiod="rx")
        self.assertEqual(block["tier"], "T6")
        self.assertEqual(block["radiod_id"], "rx")
        self.assertEqual(block["channels"], {"total": 2, "anchored": 2, "applied": 2})

    def test_one_uncorrected_source_makes_the_instance_report_null(self):
        workers = [_worker(_anchor(4_250_000)), _worker(_anchor(None, source="rtp_to_utc"))]
        self.assertIsNone(applied_state_for(workers, client_radiod="rx"))

    def test_worker_without_a_pipeline_counts_in_total_only(self):
        workers = [_worker(_anchor(4_250_000)), _worker(None, running=False)]
        block = applied_state_for(workers, client_radiod="rx")
        self.assertIsNotNone(block)
        self.assertEqual(block["channels"], {"total": 2, "anchored": 1, "applied": 1})

    def test_nothing_anchored_reports_null(self):
        workers = [_worker(None), _worker(None, running=False)]
        self.assertIsNone(applied_state_for(workers, client_radiod="rx"))

    def test_block_is_json_serialisable(self):
        json.dumps(applied_state_for([_worker(_anchor(1))], client_radiod="rx"))


class PipelineWorkerExposesItsPipeline(unittest.TestCase):
    """The daemon reaches each worker's anchor through ``worker.pipeline``;
    it must name the pipeline while it runs and None once it has closed."""

    def test_pipeline_is_none_before_and_after_a_run(self):
        import threading

        class _FakePipeline:
            def __init__(self, ev):
                self.source = self
                self._ev = ev
                self.seen_while_running = None

            def frames(self):
                self.seen_while_running = worker.pipeline
                return iter(())

            def process_frame(self, _f):  # pragma: no cover
                pass

            def close(self):
                self._ev.set()

        ev = threading.Event()
        fp = _FakePipeline(ev)
        worker = _PipelineWorker(pipeline_factory=lambda: fp, name="t", stop_event=ev)
        self.assertIsNone(worker.pipeline)
        worker._run()
        self.assertIs(fp.seen_while_running, fp)
        self.assertIsNone(worker.pipeline)


class DaemonWriterTests(unittest.TestCase):
    """``HfTecRecorder._write_applied_state`` leaves the aggregate where
    inventory reads it, and never raises into the heartbeat loop."""

    def _recorder(self, root, workers):
        rec = HfTecRecorder(cfg=_cfg(status_address="rx.local"), instance="inst",
                            data_root=Path(root))
        rec._workers = workers
        return rec

    def test_mixed_state_writes_an_explicit_null(self):
        with TemporaryDirectory() as root:
            rec = self._recorder(root, [_worker(_anchor(4_250_000)), _worker(_anchor(None))])
            rec._write_applied_state()
            path = Path(root) / "inst" / "timing-authority.json"
            raw = json.loads(path.read_text())
            self.assertEqual(raw["schema"], "applied-state/v1")
            self.assertIsNone(raw["timing_authority_applied"])
            self.assertIsNone(read_applied_state(path))

    def test_all_corrected_writes_the_block_with_counts(self):
        with TemporaryDirectory() as root:
            rec = self._recorder(root, [_worker(_anchor(4_250_000)), _worker(_anchor(4_250_000))])
            rec._write_applied_state()
            block = read_applied_state(Path(root) / "inst" / "timing-authority.json")
            self.assertEqual(block["tier"], "T6")
            self.assertEqual(block["radiod_id"], "rx.local")
            self.assertEqual(block["channels"], {"total": 2, "anchored": 2, "applied": 2})

    def test_worker_in_backoff_counts_in_total_but_not_anchored(self):
        with TemporaryDirectory() as root:
            rec = self._recorder(root, [_worker(_anchor(4_250_000)), _worker(None, running=False)])
            rec._write_applied_state()
            block = read_applied_state(Path(root) / "inst" / "timing-authority.json")
            self.assertEqual(block["channels"], {"total": 2, "anchored": 1, "applied": 1})

    def test_writer_failure_never_propagates(self):
        with TemporaryDirectory() as root:
            rec = self._recorder(root, [_worker(_anchor(4_250_000))])
            with mock.patch("hf_tec.core.daemon.applied_state_for",
                            side_effect=RuntimeError("boom")):
                rec._write_applied_state()  # must not raise

    def test_heartbeat_writes_at_most_once_per_minute(self):
        with TemporaryDirectory() as root:
            rec = self._recorder(root, [_worker(_anchor(4_250_000))])
            with mock.patch.object(rec, "_write_applied_state") as w:
                rec._maybe_write_applied_state(now=1000.0)   # first call fires
                rec._maybe_write_applied_state(now=1030.0)   # 30 s later: gated
                rec._maybe_write_applied_state(now=1059.9)   # still gated
                self.assertEqual(w.call_count, 1)
                rec._maybe_write_applied_state(now=1060.0)   # 60 s: fires
                self.assertEqual(w.call_count, 2)


class PathAgreementTests(unittest.TestCase):
    """The daemon writes and inventory reads the SAME file for one config.
    Both sides must go through ``applied_state_path`` and ``instance_name``;
    a divergence here would report null for a running daemon."""

    def _contract_read_path(self, cfg, **kw):
        seen = []
        with mock.patch("hf_tec.contract.read_applied_state",
                        side_effect=lambda p: seen.append(Path(p))):
            contract.build_inventory(cfg, stations=self._stations(), **kw)
        self.assertEqual(len(seen), 1)
        return seen[0]

    @staticmethod
    def _stations():
        from hf_tec.stations import load_stations
        return load_stations(REPO_ROOT / "data" / "stations.toml")

    def test_paths_agree_with_systemd_instance(self):
        cfg = _cfg(reporter_id="AC0G-HFB", status_address="rx.local")
        rec = HfTecRecorder(cfg=cfg, instance="AC0G-HFB")
        self.assertEqual(self._contract_read_path(cfg, instance="AC0G-HFB"),
                         rec._applied_state_path())

    def test_paths_agree_falling_back_to_reporter_id(self):
        cfg = _cfg(reporter_id="AC0G-HFB", status_address="rx.local")
        rec = HfTecRecorder(cfg=cfg, instance=None)
        rec._resolve_instance()
        self.assertEqual(rec.instance, "AC0G-HFB")
        self.assertEqual(self._contract_read_path(cfg), rec._applied_state_path())

    def test_paths_agree_falling_back_to_status_address(self):
        cfg = _cfg(reporter_id=None, status_address="rx.local")
        rec = HfTecRecorder(cfg=cfg, instance=None)
        rec._resolve_instance()
        self.assertEqual(rec.instance, "rx.local")
        self.assertEqual(self._contract_read_path(cfg), rec._applied_state_path())

    def test_default_root_is_var_lib_hf_tec(self):
        from hf_tec.core.output import DEFAULT_DATA_ROOT
        self.assertEqual(applied_state_path(DEFAULT_DATA_ROOT, "x"),
                         Path("/var/lib/hf-tec/x/timing-authority.json"))
        cfg = _cfg(reporter_id="AC0G-HFB")
        self.assertEqual(self._contract_read_path(cfg),
                         Path("/var/lib/hf-tec/AC0G-HFB/timing-authority.json"))

    def test_instance_name_order(self):
        cfg = _cfg(reporter_id="rid", status_address="sa")
        self.assertEqual(instance_name(cfg, "sys"), "sys")
        self.assertEqual(instance_name(cfg), "rid")
        self.assertEqual(instance_name(_cfg(status_address="sa")), "sa")
        self.assertIsNone(instance_name(_cfg()))


if __name__ == "__main__":
    unittest.main()
