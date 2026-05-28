"""CLI entry point — argparse + subcommand dispatch.

Per the sigmond client contract §3, three subcommands MUST emit
JSON to stdout and exit 0 on success: `version`, `inventory`,
`validate`.  Long-running entry is `daemon`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import Config, load_config
from .contract import CONTRACT_VERSION, build_inventory, build_validate
from .stations import load_stations
from .version import GIT_INFO


# ---------------------------------------------------------------------------
# Logging — defer configuration so contract subcommands stay quiet on stdout.
# ---------------------------------------------------------------------------


def _configure_logging(quiet: bool) -> None:
    if quiet:
        logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
        return
    level = os.environ.get("HF_GPS_TEC_LOG_LEVEL") or os.environ.get(
        "CLIENT_LOG_LEVEL"
    ) or "INFO"
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_version(_args: argparse.Namespace) -> int:
    payload = {
        "client": "hf-gps-tec",
        "version": __version__,
        "contract_version": CONTRACT_VERSION,
        "git": dict(GIT_INFO),
    }
    print(json.dumps(payload, indent=2))
    return 0


def _degraded_inventory_payload(reason: str) -> dict:
    """A minimal-but-contract-shaped inventory payload printed when
    config can't be read.  Sigmond's ContractAdapter checks for
    parseable JSON + exit 0 to set `installed = True`, so this lets an
    unprivileged operator's `smd config show` correctly report the
    client as installed (with an issues entry explaining the degraded
    state) instead of "not installed"."""
    return {
        "client": "hf-gps-tec",
        "version": __version__,
        "git": dict(GIT_INFO),
        "contract_version": CONTRACT_VERSION,
        "config_path": None,
        "log_paths": {
            "journal": "hf-gps-tec@*",
            "file_dir": "/var/log/hf-gps-tec",
        },
        "log_level": os.environ.get("HF_GPS_TEC_LOG_LEVEL")
        or os.environ.get("CLIENT_LOG_LEVEL")
        or "INFO",
        "instances": [],
        "deps": {"git": [], "pypi": []},
        "issues": [{
            "severity": "fail",
            "instance": None,
            "message": reason,
        }],
    }


def _load_cfg(args: argparse.Namespace) -> Config | None:
    """Load config; on FileNotFoundError or PermissionError print a
    contract-shaped degraded payload (issues populated) and return
    None.  Callers decide the exit code: `inventory` exits 0 (sigmond
    needs to learn the client is installed), `validate` exits 1
    (cannot certify health without reading config)."""
    try:
        return load_config(
            path=Path(args.config) if args.config else None,
            instance=args.instance,
        )
    except FileNotFoundError as exc:
        print(json.dumps(_degraded_inventory_payload(
            f"config not found: {exc}"), indent=2))
        return None
    except PermissionError as exc:
        # Service-user-owned config (mode 0640) is unreadable by an
        # unprivileged operator running `smd config show`.  The client
        # IS installed; the caller just can't see runtime details.
        print(json.dumps(_degraded_inventory_payload(
            f"config not readable by uid={os.getuid()}: {exc} "
            f"— inventory degraded"), indent=2))
        return None


def _handle_inventory(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    if cfg is None:
        # Degraded payload was already printed by _load_cfg.  Exit 0 so
        # sigmond's ContractAdapter parses it and marks `installed=True`
        # — without this the operator-facing `smd config show` reports
        # "not installed" for a client that very much is.
        return 0
    stations = load_stations(Path(args.stations) if args.stations else None)
    print(json.dumps(build_inventory(cfg, stations), indent=2))
    return 0


def _handle_validate(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    if cfg is None:
        return 1
    stations = load_stations(Path(args.stations) if args.stations else None)
    payload = build_validate(cfg, stations)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def _handle_status(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    if cfg is None:
        return 1
    payload = {
        "client": "hf-gps-tec",
        "version": __version__,
        "config_path": str(cfg.config_path),
        "frequencies": [
            {"center_hz": f.center_hz, "enabled": f.enabled}
            for f in cfg.frequencies
        ],
        "transmitters_enabled": list(cfg.transmitters_enabled),
        # The daemon publishes richer per-pipeline status via its control
        # socket when it's running; this CLI surface is the always-on summary.
        "daemon_pid": _read_daemon_pid(),
    }
    print(json.dumps(payload, indent=2))
    return 0


def _read_daemon_pid() -> int | None:
    pid_file = Path("/run/hf-gps-tec/daemon.pid")
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _handle_daemon(args: argparse.Namespace) -> int:
    cfg = _load_cfg(args)
    if cfg is None:
        return 1
    # Import lazily so a stub config can be validated without numpy/scipy.
    from .core.daemon import HfGpsTecRecorder
    recorder = HfGpsTecRecorder(cfg=cfg, instance=args.instance)
    return recorder.run()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _add_config_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--config",
        "-c",
        default=None,
        help="Path to the recorder config TOML (default: /etc/hf-gps-tec/hf-gps-tec-config.toml).",
    )
    p.add_argument(
        "--stations",
        default=None,
        help="Path to the stations TOML (default: /etc/hf-gps-tec/stations.toml).",
    )
    p.add_argument(
        "--instance",
        default=None,
        help="Instance name (sigmond multi-instance Phase 5).",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hf-gps-tec", description="HF PRN beacon recorder")
    sub = p.add_subparsers(dest="command", required=True)

    p_version = sub.add_parser("version", help="Emit version + git sha (JSON).")
    p_version.add_argument("--json", action="store_true", default=True, help="(default)")

    p_inv = sub.add_parser("inventory", help="Emit machine-readable inventory.")
    p_inv.add_argument("--json", action="store_true", default=True, help="(default)")
    _add_config_args(p_inv)

    p_val = sub.add_parser("validate", help="Validate configuration; exit 1 on failure.")
    p_val.add_argument("--json", action="store_true", default=True, help="(default)")
    _add_config_args(p_val)

    p_status = sub.add_parser("status", help="Show runtime status (JSON).")
    _add_config_args(p_status)

    p_dae = sub.add_parser("daemon", help="Run the recorder daemon (foreground).")
    _add_config_args(p_dae)
    # No --radiod-id: per the sigmond multi-instance architecture, the
    # systemd template's @<reporter-id> ≡ --instance, and the radiod
    # binding is config-driven via the [ka9q] block.

    # Config command — CLIENT-CONTRACT §14 JSON-roundtrip surface.
    # Sigmond's in-TUI Textual wizard needs `show --json` + `apply
    # --json -`.  hf-gps-tec had no `config` subcommand at all
    # before this; $EDITOR fallback (via `smd config edit` shellout)
    # is the only edit path otherwise.
    p_cfg = sub.add_parser("config",
        help="Config show/apply (sigmond client-contract §14)")
    cfg_sub = p_cfg.add_subparsers(dest="config_command")

    p_show = cfg_sub.add_parser("show",
        help="Emit current config (TOML→JSON) on stdout")
    p_show.add_argument("--json", action="store_true", default=True)
    p_show.add_argument("--defaults", action="store_true",
        help="(accepted for forward-compat; currently a no-op)")
    _add_config_args(p_show)

    p_apply = cfg_sub.add_parser("apply",
        help="Apply a JSON payload (from stdin) to the config")
    p_apply.add_argument("--json", action="store_true", default=True)
    p_apply.add_argument("input", nargs="?", default="-",
        help="JSON payload path or `-` for stdin (default)")
    _add_config_args(p_apply)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    quiet = any(arg in {"inventory", "validate", "version"} for arg in argv[:1])
    _configure_logging(quiet=quiet)

    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "version":   _handle_version,
        "inventory": _handle_inventory,
        "validate":  _handle_validate,
        "status":    _handle_status,
        "daemon":    _handle_daemon,
        "config":    _handle_config,
    }
    return handlers[args.command](args)


def _handle_config(args: argparse.Namespace) -> int:
    """Dispatch `config {show|apply}` (CLIENT-CONTRACT §14)."""
    from . import configurator
    sub = getattr(args, "config_command", None)
    if sub == "show":
        return configurator.cmd_config_show(args)
    if sub == "apply":
        return configurator.cmd_config_apply(args)
    print("usage: hf-gps-tec config {show|apply}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
