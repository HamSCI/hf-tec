"""Pin the §19.3 (v0.8) promise: every record written to
`sigmond.hamsci_sink` via OutputSink carries `reporter_id` as a
first-class column.

The check is deliberately not against a frozen field order or
exhaustive schema — just: `reporter_id` is in both record dicts,
and its value flows from [instance].reporter_id when present (with
the systemd instance name as the documented fallback).
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_SRC = Path(__file__).resolve().parents[1] / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from hf_tec.core.output import OutputSink  # noqa: E402


class _StubCfg:
    """OutputSink only reads cfg.sinks.{local_jsonl,hamsci_sink} and
    cfg.instance.reporter_id during construction.  Stub the rest."""

    class _Sinks:
        local_jsonl = False
        hamsci_sink = False

    class _Instance:
        def __init__(self, reporter_id):
            self.reporter_id = reporter_id

    def __init__(self, reporter_id=None):
        self.sinks = self._Sinks()
        self.instance = self._Instance(reporter_id)


def _minimal_cfg(reporter_id=None):
    return _StubCfg(reporter_id)


class ReporterIdOnRecordTests(unittest.TestCase):

    def _capture_locked_record(self, cfg, instance, **kw):
        sink = OutputSink(cfg=cfg, instance=instance)
        captured = {}
        # Stub out the writers entirely; we just want the record.
        sink._locked_jsonl = mock.Mock(write=lambda ts, r: captured.update(r))
        sink._codeless_jsonl = mock.Mock(write=lambda ts, r: None)
        sink._locked_sink = None
        sink._codeless_sink = None
        # Minimal Detection-shaped object the writer doesn't introspect.
        det = mock.Mock(
            pseudorange_km=None, doppler_hz=0.0, amplitude_db=0.0,
            snr_db=0.0, noise_floor_db=0.0, lock_quality=0.0, range_bin=0,
        )
        sink.write_detection(
            tx_id="TX", rx_id="RX", radiod_id="bee1",
            frequency_hz=2_500_000,
            timestamp_utc=datetime.now(tz=timezone.utc),
            detection=det,
            **kw,
        )
        return captured

    def test_locked_record_carries_reporter_id_from_config(self):
        cfg = _minimal_cfg(reporter_id="AC0G-B1")
        record = self._capture_locked_record(cfg, instance="AC0G-B1")
        self.assertEqual(record.get("reporter_id"), "AC0G-B1",
                         "§19.3: every locked spot row MUST include reporter_id")

    def test_locked_record_falls_back_to_instance_name(self):
        """When [instance].reporter_id is None, OutputSink falls back
        to the systemd instance string — they coincide under
        MULTI-INSTANCE-ARCHITECTURE §3."""
        cfg = _minimal_cfg(reporter_id=None)
        record = self._capture_locked_record(cfg, instance="AC0G-B1")
        self.assertEqual(record.get("reporter_id"), "AC0G-B1")

    def test_locked_record_contract_version_is_current(self):
        from hf_tec.contract import CONTRACT_VERSION
        cfg = _minimal_cfg(reporter_id="AC0G-B1")
        record = self._capture_locked_record(cfg, instance="AC0G-B1")
        self.assertEqual(record.get("contract_version"), CONTRACT_VERSION)
        self.assertEqual(CONTRACT_VERSION, "0.8")


if __name__ == "__main__":
    unittest.main()
