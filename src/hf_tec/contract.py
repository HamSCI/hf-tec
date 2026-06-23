"""HamSCI client contract surface — inventory / validate JSON builders.

Authoritative spec: /opt/git/sigmond/sigmond/docs/CLIENT-CONTRACT.md.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import Config
from .stations import StationDb, load_stations
from .version import GIT_INFO
from . import __version__


CONTRACT_VERSION = "0.8"


# ---------------------------------------------------------------------------
# Inventory (CONTRACT §3)
# ---------------------------------------------------------------------------


def build_inventory(cfg: Config, stations: StationDb | None = None) -> dict:
    if stations is None:
        stations = load_stations()

    issues: list[dict] = _inventory_issues(cfg, stations)

    enabled_freqs = [int(f.center_hz) for f in cfg.frequencies if f.enabled]
    instance = cfg.instance.reporter_id or "default"
    resolved_mode = cfg.resolved_mode()

    inv_instance = {
        "instance": instance,
        "radiod_id": None,  # set by sigmond via coordination.toml
        "host": "localhost",
        "required_cores": [],
        "preferred_cores": "worker",
        "frequencies_hz": enabled_freqs,
        "ka9q_channels": len(enabled_freqs),
        "data_destination": None,
        "data_sinks": _data_sinks(cfg, resolved_mode),
        "uses_timing_calibration": False,
        "provides_timing_calibration": False,
        "timing_authority_applied": None,
        "radiod_status_dns": cfg.ka9q.status_address,
        "data_path": {"kind": "radiod-ka9q-python", "radiod_id": None},
        "control_socket": "/run/hf-tec/control.sock",
        "transmitters_enabled": list(cfg.transmitters_enabled),
        "mode_configured": cfg.mode.mode,
        "mode_resolved":   resolved_mode,
    }

    return {
        "client": "hf-tec",
        "version": __version__,
        "git": dict(GIT_INFO),
        "contract_version": CONTRACT_VERSION,
        "config_path": str(cfg.config_path),
        "log_paths": {
            "journal": "hf-tec@*",
            "file_dir": "/var/log/hf-tec",
        },
        "log_level": os.environ.get("HF_TEC_LOG_LEVEL")
        or os.environ.get("CLIENT_LOG_LEVEL")
        or "INFO",
        "instances": [inv_instance],
        "deps": {"git": [], "pypi": []},
        "issues": issues,
    }


def _data_sinks(cfg: Config, resolved_mode: str) -> list[dict]:
    sinks = []
    subdir = "codeless" if resolved_mode == "codeless" else "locked"
    table = "hf_tec_codeless.spots" if resolved_mode == "codeless" else "hf_tec.spots"
    if cfg.sinks.local_jsonl:
        sinks.append({
            "kind": "file",
            "target": f"/var/lib/hf-tec/<radiod>/{subdir}",
            "schema_ref": f"hf_tec.{subdir}.jsonl.v1",
            "retention_days": 0,
            "mb_per_day": 0,
        })
    if cfg.sinks.hamsci_sink:
        sinks.append({
            "kind": "sqlite",
            "target": "/var/lib/sigmond/sink.db",
            "schema_ref": table,
            "retention_days": 0,
            "mb_per_day": 0,
        })
    return sinks


def _inventory_issues(cfg: Config, stations: StationDb) -> list[dict]:
    issues: list[dict] = []
    if not cfg.ka9q.status_address:
        issues.append({
            "severity": "warn",
            "instance": cfg.instance.reporter_id or "default",
            "message": "[ka9q].status_address is empty (no radiod binding)",
        })
    if not cfg.transmitters_enabled:
        issues.append({
            "severity": "warn",
            "instance": cfg.instance.reporter_id or "default",
            "message": "no transmitters enabled — recorder will idle",
        })
    unknown_tx = [t for t in cfg.transmitters_enabled if t not in stations.transmitters]
    if unknown_tx:
        issues.append({
            "severity": "warn",
            "instance": cfg.instance.reporter_id or "default",
            "message": (
                f"transmitters {unknown_tx} not in stations DB "
                f"({stations.source}); add their entry first"
            ),
        })
    if not _prn_spec_present():
        resolved = cfg.resolved_mode()
        if resolved == "codeless":
            msg = (
                "PRN code generator is a STUB; daemon will run in code-free "
                "detection mode (100-ms autocorrelation) — confirms beacon "
                "presence and recovers Doppler, but cannot resolve per-Tx "
                "pseudorange.  Drop in the real PRN spec at "
                "core/correlate.py:generate_prn_code() to upgrade to locked "
                "mode (see docs/RECEIVER.md §6)."
            )
        else:
            msg = (
                "PRN code generator is a STUB and mode is 'locked' — the "
                "recorder will produce locked-mode records derived from a "
                "fake code that do not correspond to real over-the-air "
                "signals.  Either replace generate_prn_code() with the "
                "operator-supplied spec or set [mode] mode = 'codeless'."
            )
        issues.append({
            "severity": "warn",
            "instance": cfg.instance.reporter_id or "default",
            "message": msg,
        })
    return issues


def _prn_spec_present() -> bool:
    """Whether a real PRN spec has been wired up.

    Returns False whenever `core.correlate` still exports the stub flag
    `PRN_IS_STUB = True`.  Replaced wholesale when the JRO spec lands.
    """
    try:
        from .core.correlate import PRN_IS_STUB
    except Exception:
        return False
    return not bool(PRN_IS_STUB)


# ---------------------------------------------------------------------------
# Validate (CONTRACT §3)
# ---------------------------------------------------------------------------


def build_validate(cfg: Config, stations: StationDb | None = None) -> dict:
    if stations is None:
        stations = load_stations()

    issues = _inventory_issues(cfg, stations)

    if not cfg.frequencies:
        issues.append({
            "severity": "fail",
            "instance": cfg.instance.reporter_id or "default",
            "message": "no [[frequency]] blocks configured",
        })
    proc = cfg.processing
    # Degenerate [processing] values would otherwise raise ZeroDivisionError
    # below (and crash the daemon at startup).  Report them as a fail and skip
    # the divide-dependent checks so `validate --json` stays well-formed.
    if proc.chip_microseconds <= 0 or proc.code_chips <= 0:
        issues.append({
            "severity": "fail",
            "instance": cfg.instance.reporter_id or "default",
            "message": (
                f"[processing] chip_microseconds ({proc.chip_microseconds}) "
                f"and code_chips ({proc.code_chips}) must both be > 0"
            ),
        })
        return {"ok": False, "issues": issues}

    chip_rate_hz = 1_000_000 // proc.chip_microseconds  # samples per second at 1 sample/chip
    for f in cfg.frequencies:
        if not f.enabled:
            continue
        if f.sample_rate_hz % chip_rate_hz != 0:
            issues.append({
                "severity": "warn",
                "instance": cfg.instance.reporter_id or "default",
                "message": (
                    f"frequency {f.center_hz} Hz sample_rate {f.sample_rate_hz} "
                    f"Hz is not an integer multiple of the chip rate "
                    f"({chip_rate_hz} Hz); correlator will refuse to start"
                ),
            })

    expected_code_period_ms = proc.chip_microseconds * proc.code_chips // 1000
    if expected_code_period_ms != proc.code_period_ms:
        issues.append({
            "severity": "fail",
            "instance": cfg.instance.reporter_id or "default",
            "message": (
                f"[processing] inconsistent: chip_us * code_chips / 1000 = "
                f"{expected_code_period_ms} ms but code_period_ms = "
                f"{proc.code_period_ms} ms"
            ),
        })
    if proc.coherent_reps * proc.code_period_ms != proc.coherent_seconds * 1000:
        issues.append({
            "severity": "fail",
            "instance": cfg.instance.reporter_id or "default",
            "message": (
                f"[processing] inconsistent: coherent_reps * code_period_ms = "
                f"{proc.coherent_reps * proc.code_period_ms} ms but "
                f"coherent_seconds * 1000 = {proc.coherent_seconds * 1000} ms"
            ),
        })

    ok = not any(i.get("severity") == "fail" for i in issues)
    return {"ok": ok, "issues": issues}
