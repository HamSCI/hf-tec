"""Contract surface tests: inventory and validate JSON shape + content."""

from __future__ import annotations

from pathlib import Path

from hf_tec import config as cfgmod
from hf_tec import contract


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_default_cfg():
    return cfgmod.load_config(
        REPO_ROOT / "config" / "hf-tec-config.toml.template"
    )


def _load_default_stations():
    from hf_tec.stations import load_stations
    return load_stations(REPO_ROOT / "data" / "stations.toml")


def test_inventory_required_fields() -> None:
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations())
    assert inv["client"] == "hf-tec"
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
    from hf_tec.core import correlate as cc
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


# ---------------------------------------------------------------------------
# §18.5: timing_authority_applied describes the labels the RUNNING daemon
# writes.  The daemon leaves its block at <data_root>/<instance>/
# timing-authority.json; inventory (another process) reports it while it
# stays fresh and null otherwise.
# ---------------------------------------------------------------------------


def _write_state(tmp_path, instance, block, now_fn=None):
    from hamsci_dsp.timing import write_applied_state
    write_applied_state(tmp_path / instance / "timing-authority.json", block, now_fn=now_fn)


def test_fresh_applied_state_is_reported_verbatim(tmp_path) -> None:
    block = {"source": "hf-timestd@gov", "tier": "T6", "sigma_ns": 4210,
             "snapshot_age_s": 1.0, "radiod_id": "rx.local",
             "channels": {"total": 1, "anchored": 1, "applied": 1}}
    _write_state(tmp_path, "default", block)
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations(),
                                   data_root=tmp_path)
    assert inv["instances"][0]["timing_authority_applied"] == block


def test_stale_applied_state_reports_null(tmp_path) -> None:
    import time
    _write_state(tmp_path, "default", {"tier": "T6"}, now_fn=lambda: time.time() - 3600)
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations(),
                                   data_root=tmp_path)
    assert inv["instances"][0]["timing_authority_applied"] is None


def test_absent_applied_state_reports_null(tmp_path) -> None:
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations(),
                                   data_root=tmp_path)
    assert inv["instances"][0]["timing_authority_applied"] is None


def test_inventory_reads_the_named_instance(tmp_path) -> None:
    """systemd's --instance names the daemon's directory; inventory must
    read from that directory, not from the config-derived fallback."""
    block = {"tier": "T6", "radiod_id": "rx.local"}
    _write_state(tmp_path, "AC0G-HFB", block)
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations(),
                                   instance="AC0G-HFB", data_root=tmp_path)
    assert inv["instances"][0]["instance"] == "AC0G-HFB"
    assert inv["instances"][0]["timing_authority_applied"] == block


def test_capability_is_declared() -> None:
    """hf-tec anchors through acquire_anchor_utc and so subscribes whenever
    an authority is published (§3: capability, not the active mode)."""
    inv = contract.build_inventory(_load_default_cfg(), _load_default_stations())
    assert inv["instances"][0]["uses_timing_calibration"] is True
