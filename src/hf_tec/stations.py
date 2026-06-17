"""Tx/Rx site database loader.

The default station list ships in `data/stations.toml`; the installer
copies it to `/etc/hf-tec/stations.toml`.  Operators add
new sites by editing the installed copy.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Per-deploy install puts stations.toml under a subdirectory so it isn't
# picked up by sigmond's instance scanner, which globs /etc/<client>/*.toml
# and treats every match (except legacy config names) as an instance config.
DEFAULT_STATIONS_PATH = Path("/etc/hf-tec/data/stations.toml")
# Legacy/v0.1.0 install location — kept as a fallback so an upgrade
# without re-running install.sh still works until the operator moves
# the file.
LEGACY_STATIONS_PATH = Path("/etc/hf-tec/stations.toml")
REPO_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "stations.toml"


@dataclass(frozen=True)
class Station:
    """One transmit or receive site."""
    site_id: str
    name: str
    kind: str               # "tx" or "rx"
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    frequencies_hz: tuple[int, ...] = ()
    power_watts: Optional[float] = None
    antenna: Optional[str] = None
    notes: Optional[str] = None
    # Per-Tx integer seed passed to Hysell's
    # create_pseudo_random_code(clen, seed) routine.  Required to
    # generate the correct PRN replica for that transmitter; if None
    # the recorder cannot correlate against this Tx and will skip it.
    prn_seed: Optional[int] = None
    status: Optional[str] = None


@dataclass(frozen=True)
class StationDb:
    """Parsed station database."""
    transmitters: dict[str, Station] = field(default_factory=dict)
    receivers: dict[str, Station] = field(default_factory=dict)
    source: Optional[Path] = None

    def transmitter(self, site_id: str) -> Optional[Station]:
        return self.transmitters.get(site_id.upper())

    def all_tx_frequencies(self) -> set[int]:
        out: set[int] = set()
        for tx in self.transmitters.values():
            out.update(tx.frequencies_hz)
        return out


def _parse_station(site_id: str, block: dict, kind: str) -> Station:
    return Station(
        site_id=site_id,
        name=str(block.get("name", site_id)),
        kind=kind,
        latitude_deg=float(block.get("latitude_deg", 0.0)),
        longitude_deg=float(block.get("longitude_deg", 0.0)),
        altitude_m=float(block.get("altitude_m", 0.0)),
        frequencies_hz=tuple(int(f) for f in block.get("frequencies_hz", ())),
        power_watts=(float(block["power_watts"]) if "power_watts" in block else None),
        antenna=block.get("antenna"),
        notes=block.get("notes"),
        prn_seed=(int(block["prn_seed"]) if "prn_seed" in block else None),
        status=block.get("status"),
    )


def load_stations(path: Optional[Path] = None) -> StationDb:
    """Load a station database from TOML.

    Search order if `path` is None:
      1. `/etc/hf-tec/data/stations.toml` (current install location)
      2. `/etc/hf-tec/stations.toml` (legacy / pre-fix location)
      3. the repo's `data/stations.toml` (development fallback)
    """
    candidates = (
        [path] if path is not None
        else [DEFAULT_STATIONS_PATH, LEGACY_STATIONS_PATH, REPO_DEFAULT_PATH]
    )
    for cand in candidates:
        if cand is None or not cand.exists():
            continue
        with open(cand, "rb") as f:
            raw = tomllib.load(f)
        tx_block = raw.get("transmitters", {}) or {}
        rx_block = raw.get("receivers", {}) or {}
        return StationDb(
            transmitters={k: _parse_station(k, v, "tx") for k, v in tx_block.items()},
            receivers={k: _parse_station(k, v, "rx") for k, v in rx_block.items()},
            source=cand,
        )
    return StationDb()
