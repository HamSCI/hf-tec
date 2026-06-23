"""TOML configuration loader for hf-tec.

Per the sigmond client contract, `validate --json` is the
authoritative check of a deployed config.  This module loads + lightly
type-checks; serious cross-field validation lives in
`hf_tec.contract`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG_PATH = Path("/etc/hf-tec/hf-tec-config.toml")


@dataclass(frozen=True)
class StationConfig:
    station_id: str = "AC0G-HFB"
    latitude_deg: float = 0.0
    longitude_deg: float = 0.0
    altitude_m: float = 0.0
    operator_callsign: str = ""


@dataclass(frozen=True)
class Ka9qConfig:
    status_address: str = ""
    filter_guard_hz: int = 1500
    # Seconds of I/Q silence before the source self-restarts (re-subscribes).
    # Guards against a radiod that stops delivering samples without closing
    # the stream.  0 disables the watchdog.
    stall_timeout_s: float = 30.0


@dataclass(frozen=True)
class FrequencyConfig:
    center_hz: int
    sample_rate_hz: int = 100_000
    enabled: bool = True


@dataclass(frozen=True)
class ProcessingConfig:
    chip_microseconds: int = 10
    code_chips: int = 10_000
    code_period_ms: int = 100
    coherent_reps: int = 100
    coherent_seconds: int = 10
    incoherent_windows: int = 6
    snr_threshold_db: float = 8.0
    max_pseudorange_km: float = 15_000.0
    min_pseudorange_km: float = 100.0
    # Code-free detection parameters (used when mode resolves to "codeless").
    codeless_integration_seconds: float = 60.0
    codeless_detection_threshold_db: float = 6.0


@dataclass(frozen=True)
class ModeConfig:
    """How the daemon chooses between code-free and locked operation.

    ``mode = "auto"``     — codeless while ``correlate.PRN_IS_STUB``
                            is True, locked once the real PRN spec lands.
    ``mode = "codeless"`` — always run the code-free detector.
    ``mode = "locked"``   — always run the PRN correlator (will produce
                            non-useful records while the PRN is stubbed).
    """
    mode: str = "auto"


@dataclass(frozen=True)
class SinksConfig:
    local_jsonl: bool = True
    hamsci_sink: bool = True
    jro_out_mod: bool = False


@dataclass(frozen=True)
class InstanceConfig:
    reporter_id: Optional[str] = None


@dataclass(frozen=True)
class Config:
    station: StationConfig
    ka9q: Ka9qConfig
    frequencies: tuple[FrequencyConfig, ...]
    processing: ProcessingConfig
    mode: ModeConfig
    transmitters_enabled: tuple[str, ...]
    sinks: SinksConfig
    instance: InstanceConfig
    config_path: Path
    raw: dict = field(default_factory=dict, repr=False)

    def resolved_mode(self) -> str:
        """Resolve "auto" mode into "codeless" or "locked" at runtime.

        Imported lazily to avoid pulling numpy into modules that only
        care about config loading (e.g. CLI startup before the daemon
        is built).
        """
        if self.mode.mode in {"codeless", "locked"}:
            return self.mode.mode
        try:
            from .core.correlate import PRN_IS_STUB
        except Exception:
            PRN_IS_STUB = True
        return "codeless" if PRN_IS_STUB else "locked"


def resolve_config_path(
    explicit: Optional[Path] = None,
    instance: Optional[str] = None,
) -> Path:
    """Resolve which config file to load.

    Order matches sigmond Phase 5 multi-instance:
      1. `--config` argument if provided.
      2. `TIMESTD_BEACON_CONFIG` / `HF_TEC_CONFIG` env var.
      3. `/etc/hf-tec/<instance>.toml` if instance given.
      4. `/etc/hf-tec/hf-tec-config.toml`.
    """
    if explicit is not None:
        return explicit
    env = os.environ.get("HF_TEC_CONFIG")
    if env:
        return Path(env)
    if instance:
        per_instance = Path(f"/etc/hf-tec/{instance}.toml")
        if per_instance.exists():
            return per_instance
    return DEFAULT_CONFIG_PATH


def load_config(
    path: Optional[Path] = None,
    instance: Optional[str] = None,
) -> Config:
    """Load and parse a config file.  Raises FileNotFoundError if missing."""
    resolved = resolve_config_path(path, instance)
    with open(resolved, "rb") as f:
        raw = tomllib.load(f)

    station_raw = raw.get("station", {}) or {}
    station = StationConfig(
        station_id=str(station_raw.get("station_id", "AC0G-HFB")),
        latitude_deg=float(station_raw.get("latitude_deg", 0.0)),
        longitude_deg=float(station_raw.get("longitude_deg", 0.0)),
        altitude_m=float(station_raw.get("altitude_m", 0.0)),
        operator_callsign=str(station_raw.get("operator_callsign", "")),
    )

    ka9q_raw = raw.get("ka9q", {}) or {}
    ka9q = Ka9qConfig(
        status_address=str(ka9q_raw.get("status_address", "")),
        filter_guard_hz=int(ka9q_raw.get("filter_guard_hz", 1500)),
        stall_timeout_s=float(ka9q_raw.get("stall_timeout_s", 30.0)),
    )

    freq_blocks = raw.get("frequency", []) or []
    frequencies = tuple(
        FrequencyConfig(
            center_hz=int(fb["center_hz"]),
            sample_rate_hz=int(fb.get("sample_rate_hz", 100_000)),
            enabled=bool(fb.get("enabled", True)),
        )
        for fb in freq_blocks
    )

    proc_raw = raw.get("processing", {}) or {}
    processing = ProcessingConfig(
        chip_microseconds=int(proc_raw.get("chip_microseconds", 10)),
        code_chips=int(proc_raw.get("code_chips", 10_000)),
        code_period_ms=int(proc_raw.get("code_period_ms", 100)),
        coherent_reps=int(proc_raw.get("coherent_reps", 100)),
        coherent_seconds=int(proc_raw.get("coherent_seconds", 10)),
        incoherent_windows=int(proc_raw.get("incoherent_windows", 6)),
        snr_threshold_db=float(proc_raw.get("snr_threshold_db", 8.0)),
        max_pseudorange_km=float(proc_raw.get("max_pseudorange_km", 15_000.0)),
        min_pseudorange_km=float(proc_raw.get("min_pseudorange_km", 100.0)),
        codeless_integration_seconds=float(
            proc_raw.get("codeless_integration_seconds", 60.0)
        ),
        codeless_detection_threshold_db=float(
            proc_raw.get("codeless_detection_threshold_db", 6.0)
        ),
    )

    mode_raw = raw.get("mode", {}) or {}
    mode_value = str(mode_raw.get("mode", "auto")).lower()
    if mode_value not in {"auto", "codeless", "locked"}:
        raise ValueError(
            f"[mode] mode must be one of 'auto', 'codeless', 'locked'; got {mode_value!r}"
        )
    mode_cfg = ModeConfig(mode=mode_value)

    tx_raw = raw.get("transmitters", {}) or {}
    transmitters_enabled = tuple(str(t).upper() for t in tx_raw.get("enabled", ()))

    sinks_raw = raw.get("sinks", {}) or {}
    sinks = SinksConfig(
        local_jsonl=bool(sinks_raw.get("local_jsonl", True)),
        hamsci_sink=bool(sinks_raw.get("hamsci_sink", True)),
        jro_out_mod=bool(sinks_raw.get("jro_out_mod", False)),
    )

    instance_raw = raw.get("instance", {}) or {}
    instance_cfg = InstanceConfig(
        reporter_id=instance_raw.get("reporter_id"),
    )

    return Config(
        station=station,
        ka9q=ka9q,
        frequencies=frequencies,
        processing=processing,
        mode=mode_cfg,
        transmitters_enabled=transmitters_enabled,
        sinks=sinks,
        instance=instance_cfg,
        config_path=resolved,
        raw=raw,
    )
