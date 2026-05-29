"""Config-loader smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hf_gps_tec import config as cfgmod


REPO_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "hf-gps-tec-config.toml.template"
)


def test_template_loads_cleanly(tmp_path: Path) -> None:
    """The shipped template must parse without errors."""
    cfg = cfgmod.load_config(REPO_TEMPLATE)
    assert cfg.station.station_id == "AC0G-HFB"
    assert len(cfg.frequencies) == 2
    assert {f.center_hz for f in cfg.frequencies} == {2_900_000, 3_400_000}
    assert cfg.transmitters_enabled == ("POKER_FLAT", "GAKONA", "PALMER", "CORNELL")


def test_processing_consistency_checks_present(tmp_path: Path) -> None:
    """The processing parameters in the template should be internally consistent."""
    cfg = cfgmod.load_config(REPO_TEMPLATE)
    proc = cfg.processing
    assert proc.chip_microseconds * proc.code_chips == proc.code_period_ms * 1000
    assert proc.coherent_reps * proc.code_period_ms == proc.coherent_seconds * 1000


def test_resolve_config_path_prefers_explicit(tmp_path: Path) -> None:
    p = tmp_path / "custom.toml"
    p.write_text("")
    assert cfgmod.resolve_config_path(explicit=p) == p


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cfgmod.load_config(tmp_path / "nope.toml")
