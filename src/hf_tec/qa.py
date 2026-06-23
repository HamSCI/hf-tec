"""Signal-quality diagnostic: "are we receiving real signal, or noise?"

The core idea is Palmer-as-null-control.  Palmer is a known Hysell
transmitter that is currently down for maintenance (Hysell, 2026-05-29),
so any detection of Palmer is by definition a false positive arising
from PRN-replica autocorrelation against receive-side noise.  Its
detection rate is therefore the noise-floor reference for the recorder
running at this site, this minute.

A SIGNAL verdict for Poker Flat or Gakona requires BOTH:

  1. Detection rate exceeds Palmer's by `SIGNAL_VS_PALMER`× (default 3),
     i.e. we are catching real signal above the false-positive floor.
  2. At least `SIGNAL_PCT_IN_BIN` (default 30 %) of detections land
     within ±RANGE_TOLERANCE_KM of the expected first-hop range bin,
     i.e. the detected delays correspond to a plausible ionospheric
     path.  Noise correlations spread uniformly across all 10 000 range
     bins; real signal clusters at the geometric great-circle range
     plus the F-layer climb-and-descent (~3 % overhead).

WEAK is one-of, NULL is neither.  Palmer itself is reported as the
null-control reference with no verdict of its own.

Upgrade path: when we want MUF feasibility, IRI-driven seasonal/
diurnal variation, per-frequency MUF check, or solar-terminator
overlay, factor ``hf_timestd.core.propagation_model.HFPropagationModel``
out of hf-timestd into a shared sigmond library and import from there.
For the current diagnostic question (signal vs noise floor), the
static F-layer height below is well within the ±RANGE_TOLERANCE_KM
window — a 3 % refinement does not change a NULL → SIGNAL verdict.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .config import Config, load_config
from .stations import Station, StationDb, load_stations


# ---------------------------------------------------------------------------
# Tuneable thresholds — module constants by design (per qa design doc).
# ---------------------------------------------------------------------------

#: Detection rate ratio (operational Tx / Palmer null-control) above
#: which we declare we are catching real signal above the noise floor.
SIGNAL_VS_PALMER: float = 3.0

#: Fraction of detections that must land within ±RANGE_TOLERANCE_KM of
#: the expected first-hop range, for a SIGNAL verdict.
SIGNAL_PCT_IN_BIN: float = 0.30

#: Lower bar for WEAK (vs SIGNAL).  Satisfying one of the two WEAK_*
#: gates earns WEAK; satisfying both SIGNAL_* gates earns SIGNAL.
WEAK_VS_PALMER: float = 1.5
WEAK_PCT_IN_BIN: float = 0.10

#: F-layer reflection height (km) used for the first-hop expected-range
#: prediction.  300 km is the textbook mid-latitude default; northern
#: high-latitude paths can sit closer to 250 km in summer.  See the
#: module docstring for the upgrade path to a physics-tier model.
F_LAYER_HEIGHT_KM: float = 300.0

#: Tolerance window around the expected first-hop range for the
#: "detection clusters at the right delay" check.  ±200 km is
#: ±~130 range bins at the 10-µs-chip 1500 m/bin resolution.
RANGE_TOLERANCE_KM: float = 200.0

#: Earth radius (km) for haversine.  Spherical-Earth approximation;
#: ~0.3 % error at HF beacon scales.
EARTH_RADIUS_KM: float = 6371.0

#: Default data root for locked-mode JSONL records.
DEFAULT_DATA_ROOT: Path = Path("/var/lib/hf-tec")

#: Default config root for per-instance file discovery.
DEFAULT_ETC_ROOT: Path = Path("/etc/hf-tec")

#: Tx statuses that should NEVER produce real signal.  Palmer is in
#: this set today; Cornell is too (planned, not yet on-air).  Operational
#: sites in this dict's complement are the ones we score for verdict.
NULL_TX_STATUSES: frozenset[str] = frozenset({
    "down-for-maintenance",
    "planned",
    "decommissioned",
})


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (km) between two lat/lon points (degrees).

    Spherical-Earth approximation; accurate to ~0.3 % at HF beacon
    scales.  No external dependencies — pure stdlib.
    """
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def expected_first_hop_range_km(
    gc_km: float,
    f_layer_height_km: float = F_LAYER_HEIGHT_KM,
) -> float:
    """Predicted slant-range (km) of a one-hop F-layer return.

    For a great-circle baseline of ``gc_km`` reflecting off the F layer
    at altitude ``f_layer_height_km``, the slant path length is
    approximately ``sqrt(gc_km² + 4·h²)``.  Treats the Earth as flat
    for the hop geometry; accurate to ~3 % at gc_km = 5000 km, h = 300 km.
    """
    return math.sqrt(gc_km ** 2 + 4.0 * f_layer_height_km ** 2)


def range_km_to_bin(range_km: float, chip_microseconds: float) -> int:
    """Convert a slant range (km) to a recorder range-bin index.

    The recorder's bin spacing is ``c · chip_duration / 2``.  At the
    current 10-µs chip that is 1500 m/bin (1.5 km/bin); at the planned
    20-µs chip it would be 3000 m/bin (3.0 km/bin).
    """
    # Speed of light in km/s — the km/s form of coherent.SPEED_OF_LIGHT_M_S
    # (2.99792458e8 m/s).  Kept as a literal so qa stays numpy-free, but the
    # value must match the detector's so expected_bin lines up with range_bin.
    sol_km_s = 2.99792458e5
    bin_spacing_km = sol_km_s * (chip_microseconds * 1e-6) / 2.0
    return int(range_km / bin_spacing_km)


# ---------------------------------------------------------------------------
# Duration parser for --since
# ---------------------------------------------------------------------------


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def parse_duration(s: str) -> timedelta:
    """Parse ``\\d+[smhd]`` (e.g. ``1h``, ``30m``, ``7d``) into timedelta.

    Raises ValueError for unrecognised input.
    """
    m = _DURATION_RE.match(s)
    if not m:
        raise ValueError(
            f"unparseable duration {s!r} (expected like 30m, 1h, 24h, 7d)"
        )
    n, unit = int(m.group(1)), m.group(2).lower()
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]


# ---------------------------------------------------------------------------
# Instance discovery
# ---------------------------------------------------------------------------


#: Per-instance config files are ``/etc/hf-tec/<reporter_id>.toml``.
#: Anything matching these basename patterns is NOT a per-instance file.
_NON_INSTANCE_CONFIG_NAMES: frozenset[str] = frozenset({
    "hf-tec-config.toml",       # legacy single-instance template
})

_NON_INSTANCE_SUFFIX_RE = re.compile(
    r"\.(legacy|bak|orig)(\..*)?$",
    re.IGNORECASE,
)


def discover_instances(etc_root: Path = DEFAULT_ETC_ROOT) -> list[str]:
    """List per-instance reporter_ids by scanning ``/etc/hf-tec/``.

    Returns an empty list if the directory is missing or unreadable —
    caller decides whether that is an error.
    """
    try:
        candidates = sorted(etc_root.glob("*.toml"))
    except (PermissionError, OSError):
        return []
    out: list[str] = []
    for path in candidates:
        if path.name in _NON_INSTANCE_CONFIG_NAMES:
            continue
        if _NON_INSTANCE_SUFFIX_RE.search(path.name):
            continue
        out.append(path.stem)
    return out


def resolve_instance(
    explicit: Optional[str],
    etc_root: Path = DEFAULT_ETC_ROOT,
) -> str:
    """Pick which per-instance reporter_id to operate on.

    - If ``explicit`` given, return it (caller still has to find the file).
    - If exactly one instance file in ``etc_root``, autopick it.
    - If zero or more than one, raise ValueError with a helpful message.
    """
    if explicit:
        return explicit
    found = discover_instances(etc_root)
    if not found:
        raise ValueError(
            f"no per-instance config files found under {etc_root}; "
            "pass --instance explicitly or check that the recorder is installed"
        )
    if len(found) > 1:
        raise ValueError(
            f"multiple instances found under {etc_root}: {', '.join(found)}; "
            "disambiguate with --instance <reporter_id>"
        )
    return found[0]


# ---------------------------------------------------------------------------
# JSONL reader
# ---------------------------------------------------------------------------


def _day_range(since: datetime, until: datetime) -> Iterator[tuple[int, int, int]]:
    """Yield (year, month, day) tuples covering [since.date(), until.date()]."""
    d = since.date()
    last = until.date()
    while d <= last:
        yield d.year, d.month, d.day
        d = d + timedelta(days=1)


def iter_locked_records(
    data_root: Path,
    reporter_id: str,
    since: datetime,
    until: datetime,
) -> Iterator[dict]:
    """Yield locked-mode JSONL records with ``time`` in [since, until]."""
    spool = data_root / reporter_id / "locked"
    for y, m, d in _day_range(since, until):
        path = spool / f"{y:04d}" / f"{m:02d}" / f"{d:02d}.jsonl"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = rec.get("time")
                if not isinstance(t, str):
                    continue
                try:
                    ts = datetime.fromisoformat(t)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if since <= ts <= until:
                    yield rec


def has_codeless_records(
    data_root: Path,
    reporter_id: str,
    since: datetime,
    until: datetime,
) -> bool:
    """Cheap probe: is the recorder emitting codeless instead of locked?

    Used to give the operator a clear error when ``qa`` is run against
    a codeless deployment (Palmer-as-null only works with per-Tx
    attribution, which codeless mode does not provide).
    """
    spool = data_root / reporter_id / "codeless"
    for y, m, d in _day_range(since, until):
        path = spool / f"{y:04d}" / f"{m:02d}" / f"{d:02d}.jsonl"
        if path.exists() and path.stat().st_size > 0:
            return True
    return False


# ---------------------------------------------------------------------------
# Tally + verdict
# ---------------------------------------------------------------------------


@dataclass
class CellStats:
    """Per-(tx_id, frequency_hz) detection statistics within the window."""
    tx_id: str
    frequency_hz: int
    count: int = 0
    count_in_bin: int = 0  # detections within ±RANGE_TOLERANCE_KM of expected

    @property
    def pct_in_bin(self) -> float:
        return self.count_in_bin / self.count if self.count else 0.0


@dataclass
class TxGeometry:
    """Per-Tx geometric setup for verdict scoring."""
    tx_id: str
    status: str
    is_null_control: bool          # True for Palmer / planned / decommissioned
    gc_km: float
    expected_range_km: float


def _build_tx_geometries(
    cfg: Config,
    stations: StationDb,
) -> dict[str, TxGeometry]:
    """One TxGeometry per enabled transmitter that has a known location."""
    rx_lat = cfg.station.latitude_deg
    rx_lon = cfg.station.longitude_deg
    out: dict[str, TxGeometry] = {}
    for tx_id in cfg.transmitters_enabled:
        tx = stations.transmitter(tx_id)
        if tx is None:
            continue
        gc = haversine_km(rx_lat, rx_lon, tx.latitude_deg, tx.longitude_deg)
        exp = expected_first_hop_range_km(gc)
        status = (tx.status or "unknown").lower()
        out[tx_id] = TxGeometry(
            tx_id=tx_id,
            status=status,
            is_null_control=status in NULL_TX_STATUSES,
            gc_km=gc,
            expected_range_km=exp,
        )
    return out


def _tally(
    records: Iterable[dict],
    geometries: dict[str, TxGeometry],
) -> dict[tuple[str, int], CellStats]:
    cells: dict[tuple[str, int], CellStats] = {}
    for rec in records:
        tx_id = str(rec.get("tx_id", "")).upper()
        if not tx_id:
            continue
        freq = int(rec.get("frequency_hz", 0))
        if not freq:
            continue
        key = (tx_id, freq)
        cell = cells.setdefault(key, CellStats(tx_id=tx_id, frequency_hz=freq))
        cell.count += 1
        geom = geometries.get(tx_id)
        if geom is None:
            continue
        psr = rec.get("pseudorange_km")
        if not isinstance(psr, (int, float)):
            continue
        if abs(float(psr) - geom.expected_range_km) <= RANGE_TOLERANCE_KM:
            cell.count_in_bin += 1
    return cells


def _verdict(
    cell: CellStats,
    palmer_rate: Optional[float],
    window_minutes: float,
    is_null_control: bool,
) -> tuple[str, Optional[float], float]:
    """Score one cell against thresholds.

    Returns (verdict, vs_palmer_ratio_or_None, det_per_min).
    """
    det_per_min = cell.count / window_minutes if window_minutes > 0 else 0.0
    if is_null_control:
        return "null-ref", None, det_per_min
    vs_palmer: Optional[float]
    if palmer_rate is None or palmer_rate == 0.0:
        vs_palmer = None
    else:
        vs_palmer = det_per_min / palmer_rate
    pct = cell.pct_in_bin
    signal_rate_ok = (vs_palmer is not None and vs_palmer >= SIGNAL_VS_PALMER)
    signal_bin_ok = pct >= SIGNAL_PCT_IN_BIN
    if signal_rate_ok and signal_bin_ok:
        return "SIGNAL", vs_palmer, det_per_min
    weak_rate_ok = (vs_palmer is not None and vs_palmer >= WEAK_VS_PALMER)
    weak_bin_ok = pct >= WEAK_PCT_IN_BIN
    if weak_rate_ok or weak_bin_ok:
        return "WEAK", vs_palmer, det_per_min
    return "NULL", vs_palmer, det_per_min


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


@dataclass
class QaResult:
    """Structured qa output.  Serialisable to JSON via ``to_dict()``."""
    instance: str
    rx_id: str
    rx_lat: float
    rx_lon: float
    window_start: datetime
    window_end: datetime
    window_minutes: float
    chip_microseconds: int
    n_records: int
    rows: list[dict] = field(default_factory=list)
    palmer_rate_per_min: Optional[float] = None
    signal_count: int = 0
    weak_count: int = 0
    null_count: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "instance": self.instance,
            "rx_id": self.rx_id,
            "rx_lat_deg": self.rx_lat,
            "rx_lon_deg": self.rx_lon,
            "window_start_utc": self.window_start.isoformat(),
            "window_end_utc": self.window_end.isoformat(),
            "window_minutes": self.window_minutes,
            "chip_microseconds": self.chip_microseconds,
            "n_records": self.n_records,
            "palmer_rate_per_min": self.palmer_rate_per_min,
            "rows": self.rows,
            "verdict_summary": {
                "signal": self.signal_count,
                "weak": self.weak_count,
                "null": self.null_count,
            },
            "thresholds": {
                "signal_vs_palmer": SIGNAL_VS_PALMER,
                "signal_pct_in_bin": SIGNAL_PCT_IN_BIN,
                "weak_vs_palmer": WEAK_VS_PALMER,
                "weak_pct_in_bin": WEAK_PCT_IN_BIN,
                "range_tolerance_km": RANGE_TOLERANCE_KM,
                "f_layer_height_km": F_LAYER_HEIGHT_KM,
            },
            "error": self.error,
        }


def run_qa(
    *,
    instance: Optional[str] = None,
    since: timedelta = timedelta(hours=1),
    aggregate: bool = False,
    data_root: Path = DEFAULT_DATA_ROOT,
    etc_root: Path = DEFAULT_ETC_ROOT,
    config_path: Optional[Path] = None,
    stations_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> QaResult:
    """Build the qa diagnostic for one instance over the trailing window.

    ``now`` is exposed so tests can pin the window to a known time.
    """
    reporter_id = resolve_instance(instance, etc_root)
    # If the caller didn't pass an explicit config path, load via
    # the per-instance file the resolver just chose.
    if config_path is None:
        config_path = etc_root / f"{reporter_id}.toml"
    cfg = load_config(path=config_path)
    stations = load_stations(stations_path)

    now_utc = (now or datetime.now(timezone.utc))
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    window_start = now_utc - since
    window_minutes = since.total_seconds() / 60.0

    result = QaResult(
        instance=reporter_id,
        rx_id=cfg.station.station_id,
        rx_lat=cfg.station.latitude_deg,
        rx_lon=cfg.station.longitude_deg,
        window_start=window_start,
        window_end=now_utc,
        window_minutes=window_minutes,
        chip_microseconds=cfg.processing.chip_microseconds,
        n_records=0,
    )

    geometries = _build_tx_geometries(cfg, stations)
    if not geometries:
        result.error = (
            "no enabled transmitters have known locations in stations.toml — "
            "nothing to score against"
        )
        return result

    records = list(iter_locked_records(data_root, reporter_id, window_start, now_utc))
    result.n_records = len(records)

    if not records:
        if has_codeless_records(data_root, reporter_id, window_start, now_utc):
            result.error = (
                "only codeless-mode records exist in this window — qa requires "
                "locked-mode records (per-Tx attribution) to use Palmer as a "
                "null control.  Run the daemon with [mode] mode = 'locked' or "
                "'auto' (with PRN_IS_STUB=False) to enable."
            )
        else:
            result.error = (
                f"no records found under "
                f"{data_root / reporter_id / 'locked'} in the last "
                f"{window_minutes:.0f} minutes"
            )
        return result

    cells = _tally(records, geometries)

    # Palmer is the null control.  Aggregate over enabled freqs to get
    # the strongest possible per-min noise-floor estimate (a per-freq
    # Palmer rate is reported in the table; the cross-Tx ratio uses
    # the matching-freq Palmer rate when --aggregate is not set, and
    # the all-freq aggregate when it is).
    palmer_per_freq: dict[int, float] = {}
    palmer_total = 0
    for (tx_id, freq), cell in cells.items():
        if (geometries.get(tx_id) and geometries[tx_id].is_null_control
                and tx_id.upper() == "PALMER"):
            palmer_per_freq[freq] = cell.count / window_minutes
            palmer_total += cell.count
    palmer_aggregate = palmer_total / window_minutes if window_minutes else 0.0
    result.palmer_rate_per_min = palmer_aggregate

    if aggregate:
        # Roll up per-freq cells into one (tx_id, "all") cell each.
        merged: dict[str, CellStats] = {}
        for (tx_id, _freq), cell in cells.items():
            agg = merged.setdefault(
                tx_id,
                CellStats(tx_id=tx_id, frequency_hz=0),  # 0 = sentinel "all"
            )
            agg.count += cell.count
            agg.count_in_bin += cell.count_in_bin
        items: list[tuple[str, int, CellStats]] = [
            (tx_id, 0, cell) for tx_id, cell in merged.items()
        ]
    else:
        items = [(tx_id, freq, cell) for (tx_id, freq), cell in cells.items()]

    # Also surface enabled Tx that had ZERO detections in the window.
    seen = {(tx_id, freq) for tx_id, freq, _ in items}
    if aggregate:
        for tx_id in geometries:
            if (tx_id, 0) not in seen:
                items.append((tx_id, 0, CellStats(tx_id=tx_id, frequency_hz=0)))
    else:
        for tx_id in geometries:
            for fc in cfg.frequencies:
                key = (tx_id, fc.center_hz)
                if key not in seen:
                    items.append((tx_id, fc.center_hz, CellStats(
                        tx_id=tx_id, frequency_hz=fc.center_hz)))

    items.sort(key=lambda x: (x[0], x[1]))

    for tx_id, freq, cell in items:
        geom = geometries.get(tx_id)
        if geom is None:
            continue
        palmer_rate = (palmer_aggregate if aggregate
                       else palmer_per_freq.get(freq, 0.0))
        verdict, vs_palmer, det_per_min = _verdict(
            cell, palmer_rate, window_minutes, geom.is_null_control)
        if verdict == "SIGNAL":
            result.signal_count += 1
        elif verdict == "WEAK":
            result.weak_count += 1
        elif verdict == "NULL":
            result.null_count += 1
        expected_bin = range_km_to_bin(
            geom.expected_range_km, cfg.processing.chip_microseconds)
        result.rows.append({
            "tx_id": tx_id,
            "status": geom.status,
            "frequency_hz": freq,
            "gc_km": round(geom.gc_km, 1),
            "expected_range_km": round(geom.expected_range_km, 1),
            "expected_bin": expected_bin,
            "n_detections": cell.count,
            "det_per_min": round(det_per_min, 3),
            "vs_palmer": (round(vs_palmer, 2) if vs_palmer is not None
                          else None),
            "pct_in_bin": round(cell.pct_in_bin, 3),
            "verdict": verdict,
        })
    return result


# ---------------------------------------------------------------------------
# Human-readable formatter
# ---------------------------------------------------------------------------


def _fmt_freq(hz: int) -> str:
    if hz == 0:
        return "all"
    if hz % 1_000_000 == 0:
        return f"{hz // 1_000_000} MHz"
    return f"{hz / 1_000_000:.3f} MHz".rstrip("0").rstrip(".")


def format_human(result: QaResult) -> str:
    """Render a QaResult as a plain-text table for terminal display."""
    lines: list[str] = []
    lines.append(
        f"hf-tec QA -- instance {result.instance}, "
        f"window: last {result.window_minutes:.0f} minutes "
        f"(UTC {result.window_start.strftime('%Y-%m-%d %H:%M')} "
        f"-> {result.window_end.strftime('%H:%M')})"
    )
    lines.append(
        f"station: {result.rx_id} @ "
        f"{result.rx_lat:.4f}N, {result.rx_lon:.4f}E"
    )
    lines.append(f"N records: {result.n_records} (locked mode)")
    lines.append("")
    if result.error:
        lines.append(f"ERROR: {result.error}")
        return "\n".join(lines)
    header = (
        f"{'tx_id':<14}{'status':<22}{'freq':<10}"
        f"{'gc_km':>7}{'exp_bin':>9}{'det/min':>9}"
        f"{'vs_palmer':>11}{'in_bin':>8}  verdict"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in result.rows:
        vs = (f"{row['vs_palmer']:.2f}x" if row['vs_palmer'] is not None
              else "  --")
        lines.append(
            f"{row['tx_id']:<14}"
            f"{row['status']:<22}"
            f"{_fmt_freq(row['frequency_hz']):<10}"
            f"{row['gc_km']:>7.0f}"
            f"{row['expected_bin']:>9d}"
            f"{row['det_per_min']:>9.2f}"
            f"{vs:>11}"
            f"{row['pct_in_bin'] * 100:>7.1f}%"
            f"  {row['verdict']}"
        )
    lines.append("")
    lines.append("Verdict summary:")
    if result.signal_count:
        lines.append(
            f"  SIGNAL on {result.signal_count} cell(s) -- "
            f"receiving real beacon above the noise floor."
        )
    elif result.weak_count:
        lines.append(
            f"  WEAK on {result.weak_count} cell(s), nothing SIGNAL -- "
            f"marginal; could be real signal at low SNR or noise-floor "
            f"variation."
        )
    else:
        lines.append(
            "  No operational Tx reached SIGNAL or WEAK threshold."
        )
        lines.append(
            "  Detection rates are at the Palmer null-control floor; "
            "delays are not clustering at the expected first-hop range."
        )
        lines.append(
            "  -> Currently receiving noise only.  At 2.9 / 3.4 MHz this is "
            "consistent with"
        )
        lines.append(
            "     D-region absorption during daylight at high northern "
            "latitudes in summer."
        )
    lines.append("")
    lines.append(
        f"Thresholds (constants in hf_tec.qa):"
    )
    lines.append(
        f"  SIGNAL: vs_palmer >= {SIGNAL_VS_PALMER}x "
        f"AND >= {SIGNAL_PCT_IN_BIN * 100:.0f}% in +/-{RANGE_TOLERANCE_KM:.0f} km of expected range"
    )
    lines.append(
        f"  WEAK:   vs_palmer >= {WEAK_VS_PALMER}x "
        f"OR  >= {WEAK_PCT_IN_BIN * 100:.0f}% in +/-{RANGE_TOLERANCE_KM:.0f} km of expected range"
    )
    return "\n".join(lines)
