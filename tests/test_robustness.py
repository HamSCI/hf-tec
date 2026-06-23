#!/usr/bin/env python3
"""Robustness / error-recovery regressions:

  * `validate --json` must report a clean fail (not raise) on degenerate
    [processing] values that would ZeroDivisionError.
  * [ka9q] stall_timeout_s round-trips through the config loader.
  * HfTecRecorder normalises a missing instance instead of crashing deep
    inside OutputSink path construction.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from hf_tec import config as C          # noqa: E402
from hf_tec.contract import build_validate  # noqa: E402
from hf_tec.stations import Station, StationDb  # noqa: E402


def _cfg(**proc_over):
    proc_kw = dict(chip_microseconds=10, code_chips=10_000, code_period_ms=100,
                   coherent_reps=100, coherent_seconds=10, incoherent_windows=6)
    proc_kw.update(proc_over)
    return C.Config(
        station=C.StationConfig(), ka9q=C.Ka9qConfig(status_address="rx"),
        frequencies=(C.FrequencyConfig(center_hz=2_900_000),),
        processing=C.ProcessingConfig(**proc_kw), mode=C.ModeConfig(mode="locked"),
        transmitters_enabled=("POKER_FLAT",), sinks=C.SinksConfig(),
        instance=C.InstanceConfig(), config_path=Path("/tmp/x.toml"),
    )


_SDB = StationDb(transmitters={"POKER_FLAT": Station(
    site_id="POKER_FLAT", name="x", kind="tx", latitude_deg=65.0,
    longitude_deg=-147.0, altitude_m=0.0, frequencies_hz=(2_900_000,), prn_seed=0)})


class TestValidateGuards(unittest.TestCase):
    def test_zero_chip_us_reports_fail_not_raises(self):
        out = build_validate(_cfg(chip_microseconds=0), _SDB)
        self.assertFalse(out["ok"])
        self.assertTrue(any(i["severity"] == "fail"
                            and "must both be > 0" in i["message"]
                            for i in out["issues"]))

    def test_zero_code_chips_reports_fail_not_raises(self):
        out = build_validate(_cfg(code_chips=0), _SDB)
        self.assertFalse(out["ok"])

    def test_healthy_config_still_ok(self):
        out = build_validate(_cfg(), _SDB)
        self.assertTrue(out["ok"], out)


class TestStallTimeoutConfig(unittest.TestCase):
    def test_default(self):
        self.assertEqual(C.Ka9qConfig().stall_timeout_s, 30.0)

    def test_parsed_from_toml(self):
        tmp = Path("/tmp/_hftec_stall.toml")
        tmp.write_text(
            '[ka9q]\nstatus_address = "rx"\nstall_timeout_s = 12.5\n'
            '[[frequency]]\ncenter_hz = 2900000\n'
        )
        try:
            cfg = C.load_config(path=tmp)
            self.assertEqual(cfg.ka9q.stall_timeout_s, 12.5)
        finally:
            tmp.unlink()


class TestInstanceFallback(unittest.TestCase):
    def test_missing_instance_falls_back_to_reporter_id(self):
        from hf_tec.core.daemon import HfTecRecorder
        cfg = C.Config(
            station=C.StationConfig(), ka9q=C.Ka9qConfig(status_address="rx"),
            frequencies=(),  # no enabled freqs -> run() returns 2 after normalising
            processing=C.ProcessingConfig(), mode=C.ModeConfig(mode="locked"),
            transmitters_enabled=(), sinks=C.SinksConfig(local_jsonl=False, hamsci_sink=False),
            instance=C.InstanceConfig(reporter_id="AC0G-B7"),
            config_path=Path("/tmp/x.toml"),
        )
        rec = HfTecRecorder(cfg=cfg, instance=None, stations=_SDB)
        rc = rec.run()                       # normalises instance, then no-freq exit
        self.assertEqual(rec.instance, "AC0G-B7")
        self.assertEqual(rc, 2)              # "no enabled frequencies"


if __name__ == "__main__":
    unittest.main()
