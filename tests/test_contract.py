"""Contract surface tests: inventory and validate JSON shape + content."""

from __future__ import annotations

from pathlib import Path

from hf_gps_tec import config as cfgmod
from hf_gps_tec import contract


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_default_cfg():
    return cfgmod.load_config(
        REPO_ROOT / "config" / "hf-gps-tec-config.toml.template"
    )


def _load_default_stations():
    from hf_gps_tec.stations import load_stations
    return load_stations(REPO_ROOT / "data" / "stations.toml")


def test_inventory_required_fields() -> None:
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations())
    assert inv["client"] == "hf-gps-tec"
    assert inv["contract_version"] == "0.8"
    assert "instances" in inv and len(inv["instances"]) == 1
    inst = inv["instances"][0]
    for key in (
        "instance", "radiod_id", "host", "frequencies_hz",
        "ka9q_channels", "data_sinks", "data_path",
    ):
        assert key in inst, f"missing inventory field: {key}"


def test_inventory_surfaces_prn_stub_warning(monkeypatch) -> None:
    """When PRN_IS_STUB=True, inventory must warn so operators see it.

    Hysell's real generator is wired in (2026-05-29), so the flag is
    normally False — monkeypatch it back to True to verify the contract
    surface would still raise the alarm if the generator ever regressed
    to a stub.
    """
    from hf_gps_tec.core import correlate as cc
    monkeypatch.setattr(cc, "PRN_IS_STUB", True)
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations())
    messages = " | ".join(i.get("message", "") for i in inv.get("issues", []))
    assert "PRN" in messages and "STUB" in messages


def test_inventory_no_prn_warning_when_real_generator_wired() -> None:
    """With the Hysell generator in place (PRN_IS_STUB=False), the
    codeless-mode warning must NOT appear in the inventory issues."""
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations())
    messages = " | ".join(i.get("message", "") for i in inv.get("issues", []))
    assert "STUB" not in messages


def test_validate_ok_with_template() -> None:
    """The shipped template should pass validate (warnings ok, no fails)."""
    payload = contract.build_validate(_load_default_cfg(), _load_default_stations())
    fails = [i for i in payload["issues"] if i.get("severity") == "fail"]
    assert fails == [], f"unexpected fail-severity issues: {fails}"
    assert payload["ok"] is True
