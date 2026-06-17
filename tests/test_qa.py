"""Tests for `hf-tec qa` — Palmer-as-null-control signal quality diagnostic."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hf_tec import qa


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------


def test_haversine_zero_distance() -> None:
    assert qa.haversine_km(0.0, 0.0, 0.0, 0.0) == pytest.approx(0.0, abs=1e-9)


def test_haversine_bee1_to_poker_flat() -> None:
    """bee1 (38.8125N, -90.4063E) to Poker Flat (65.1175N, -147.4319E).

    Independent calculation: ~4800 km.  Tolerate ±50 km — the qa
    diagnostic does not need sub-1% accuracy from a spherical-Earth
    approximation.
    """
    d = qa.haversine_km(38.8125, -90.4063, 65.1175, -147.4319)
    assert 4500 < d < 5100, f"expected ~4800 km, got {d:.1f}"


def test_haversine_antipodes_is_half_earth_circumference() -> None:
    d = qa.haversine_km(0.0, 0.0, 0.0, 180.0)
    assert d == pytest.approx(math.pi * qa.EARTH_RADIUS_KM, rel=1e-6)


def test_expected_first_hop_range_geometric_floor() -> None:
    """First-hop range MUST exceed great-circle distance."""
    gc = 5000.0
    exp = qa.expected_first_hop_range_km(gc, f_layer_height_km=300.0)
    assert exp > gc
    # And the overhead should be modest (~3 % at this scale).
    assert exp / gc < 1.05


def test_range_km_to_bin_at_10us_chip() -> None:
    """1500 m/bin at the current 10-µs chip."""
    # 1.5 km → bin 1; 1500 km → bin 1000; 15000 km → bin 10000.
    assert qa.range_km_to_bin(1.5, chip_microseconds=10) == 1
    assert qa.range_km_to_bin(1500.0, chip_microseconds=10) == 1000


def test_range_km_to_bin_at_20us_chip_halves_resolution() -> None:
    """Planned 20-µs chip → 3 km/bin."""
    assert qa.range_km_to_bin(3.0, chip_microseconds=20) == 1
    assert qa.range_km_to_bin(3000.0, chip_microseconds=20) == 1000


# ---------------------------------------------------------------------------
# Duration parser
# ---------------------------------------------------------------------------


def test_parse_duration_accepts_common_forms() -> None:
    assert qa.parse_duration("30m") == timedelta(minutes=30)
    assert qa.parse_duration("1h") == timedelta(hours=1)
    assert qa.parse_duration("24h") == timedelta(hours=24)
    assert qa.parse_duration("7d") == timedelta(days=7)


def test_parse_duration_rejects_junk() -> None:
    with pytest.raises(ValueError):
        qa.parse_duration("forever")
    with pytest.raises(ValueError):
        qa.parse_duration("5")
    with pytest.raises(ValueError):
        qa.parse_duration("")


# ---------------------------------------------------------------------------
# Instance discovery
# ---------------------------------------------------------------------------


def test_discover_instances_filters_legacy_and_backups(tmp_path: Path) -> None:
    etc = tmp_path / "hf-tec"
    etc.mkdir()
    (etc / "AC0G-B1.toml").write_text("")
    (etc / "hf-tec-config.toml").write_text("")          # legacy
    (etc / "AC0G-B1.toml.bak").write_text("")                # backup
    (etc / "AC0G-B1.toml.legacy").write_text("")             # backup
    found = qa.discover_instances(etc)
    assert found == ["AC0G-B1"]


def test_resolve_instance_autopicks_when_single(tmp_path: Path) -> None:
    etc = tmp_path / "hf-tec"
    etc.mkdir()
    (etc / "AC0G-B1.toml").write_text("")
    assert qa.resolve_instance(None, etc) == "AC0G-B1"


def test_resolve_instance_requires_flag_when_multiple(tmp_path: Path) -> None:
    etc = tmp_path / "hf-tec"
    etc.mkdir()
    (etc / "AC0G-B1.toml").write_text("")
    (etc / "AC0G-B2.toml").write_text("")
    with pytest.raises(ValueError, match="multiple instances"):
        qa.resolve_instance(None, etc)


def test_resolve_instance_explicit_is_returned_unchanged(tmp_path: Path) -> None:
    etc = tmp_path / "hf-tec"
    etc.mkdir()
    assert qa.resolve_instance("AC0G-FUTURE", etc) == "AC0G-FUTURE"


def test_resolve_instance_errors_when_empty(tmp_path: Path) -> None:
    etc = tmp_path / "hf-tec"
    etc.mkdir()
    with pytest.raises(ValueError, match="no per-instance"):
        qa.resolve_instance(None, etc)


# ---------------------------------------------------------------------------
# Synthetic-data fixtures + end-to-end verdict
# ---------------------------------------------------------------------------


# bee1 station (Missouri), used as the AC0G-HFGT receiver.
_RX_LAT = 38.8125
_RX_LON = -90.4063

# Three Alaska Tx from Hysell's 2026-05-29 email.
_TX_POKER = (65.1175, -147.4319)
_TX_GAKONA = (62.3892, -145.1358)
_TX_PALMER = (61.5656, -149.2517)


def _write_config(etc: Path, reporter_id: str) -> Path:
    """Minimal per-instance config that load_config can parse."""
    cfg = etc / f"{reporter_id}.toml"
    cfg.write_text(
        "[instance]\n"
        f'reporter_id = "{reporter_id}"\n\n'
        "[station]\n"
        f'station_id = "AC0G-HFGT"\n'
        f"latitude_deg = {_RX_LAT}\n"
        f"longitude_deg = {_RX_LON}\n\n"
        "[ka9q]\n"
        'status_address = "bee1-status.local"\n\n'
        "[mode]\n"
        'mode = "locked"\n\n'
        "[transmitters]\n"
        'enabled = ["POKER_FLAT", "GAKONA", "PALMER"]\n\n'
        "[[frequency]]\n"
        "center_hz = 2_900_000\n"
        "enabled = true\n\n"
        "[[frequency]]\n"
        "center_hz = 3_400_000\n"
        "enabled = true\n"
    )
    return cfg


def _write_stations(etc: Path) -> Path:
    """Stations DB matching the live deployed file."""
    stations = etc / "data" / "stations.toml"
    stations.parent.mkdir(parents=True, exist_ok=True)
    stations.write_text(
        f"[transmitters.POKER_FLAT]\n"
        f'status = "operational"\n'
        f"latitude_deg = {_TX_POKER[0]}\n"
        f"longitude_deg = {_TX_POKER[1]}\n"
        f"frequencies_hz = [2_900_000, 3_400_000]\n"
        f"prn_seed = 0\n\n"
        f"[transmitters.GAKONA]\n"
        f'status = "operational"\n'
        f"latitude_deg = {_TX_GAKONA[0]}\n"
        f"longitude_deg = {_TX_GAKONA[1]}\n"
        f"frequencies_hz = [2_900_000, 3_400_000]\n"
        f"prn_seed = 1\n\n"
        f"[transmitters.PALMER]\n"
        f'status = "down-for-maintenance"\n'
        f"latitude_deg = {_TX_PALMER[0]}\n"
        f"longitude_deg = {_TX_PALMER[1]}\n"
        f"frequencies_hz = [2_900_000, 3_400_000]\n"
        f"prn_seed = 2\n"
    )
    return stations


def _record(
    *, tx_id: str, freq: int, t: datetime, pseudorange_km: float,
) -> dict:
    return {
        "time": t.replace(tzinfo=timezone.utc).isoformat(),
        "mode": "locked",
        "reporter_id": "AC0G-B1",
        "tx_id": tx_id,
        "rx_id": "AC0G-HFGT",
        "radiod_id": "bee1-status.local",
        "frequency_hz": freq,
        "pseudorange_km": pseudorange_km,
        "doppler_hz": 0.5,
        "amplitude_db": -5.0,
        "snr_db": 9.0,
        "noise_floor_db": -15.0,
        "lock_quality": 0.3,
        "range_bin": int(pseudorange_km / 1.5),
        "n_hops": 1,
        "processing_version": "0.1.0",
        "contract_version": "0.8",
    }


def _spool(data_root: Path, reporter_id: str, records: list[dict]) -> None:
    """Write all records into the right daily JSONL file under data_root."""
    for rec in records:
        ts = datetime.fromisoformat(rec["time"])
        path = (data_root / reporter_id / "locked"
                / f"{ts.year:04d}" / f"{ts.month:02d}"
                / f"{ts.day:02d}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def _common_setup(tmp_path: Path) -> tuple[Path, Path, datetime]:
    etc = tmp_path / "etc"
    etc.mkdir()
    _write_config(etc, "AC0G-B1")
    _write_stations(etc)
    data_root = tmp_path / "var" / "lib" / "hf-tec"
    data_root.mkdir(parents=True)
    now = datetime(2026, 5, 29, 16, 30, tzinfo=timezone.utc)
    return etc, data_root, now


def test_qa_uniform_noise_yields_null_verdict_everywhere(tmp_path: Path) -> None:
    """Detections spread uniformly across the full 0..15000 km range
    space should produce NULL for both operational Tx and (null-ref)
    for Palmer."""
    etc, data_root, now = _common_setup(tmp_path)
    base = now - timedelta(minutes=30)

    records: list[dict] = []
    # 50 records per (tx, freq) cell spread across the 30-min half-window
    # (36 s spacing keeps the last record at base + 29.4 min, comfortably
    # inside `now`).
    for tx_id in ("POKER_FLAT", "GAKONA", "PALMER"):
        for freq in (2_900_000, 3_400_000):
            for i in range(50):
                # Uniform from 150 to 14850 km, stepping ~300 km.
                psr = 150.0 + (i % 50) * 294.0
                t = base + timedelta(seconds=36 * i)
                records.append(_record(
                    tx_id=tx_id, freq=freq, t=t, pseudorange_km=psr,
                ))
    _spool(data_root, "AC0G-B1", records)

    result = qa.run_qa(
        instance="AC0G-B1",
        since=timedelta(hours=1),
        data_root=data_root,
        etc_root=etc,
        stations_path=etc / "data" / "stations.toml",
        now=now,
    )

    assert result.error is None, result.error
    assert result.n_records == 6 * 50
    # Every operational cell should be NULL.
    op_rows = [r for r in result.rows if r["tx_id"] in ("POKER_FLAT", "GAKONA")]
    assert op_rows, "expected operational rows in output"
    assert all(r["verdict"] == "NULL" for r in op_rows), op_rows
    # Palmer rows should be the null-ref.
    palmer_rows = [r for r in result.rows if r["tx_id"] == "PALMER"]
    assert palmer_rows
    assert all(r["verdict"] == "null-ref" for r in palmer_rows)
    assert result.signal_count == 0
    assert result.weak_count == 0


def test_qa_signal_clustered_at_expected_bin_yields_signal(tmp_path: Path) -> None:
    """Detections clustered at the predicted first-hop range for Poker
    Flat AND at >>3x Palmer's rate should produce SIGNAL."""
    etc, data_root, now = _common_setup(tmp_path)
    base = now - timedelta(minutes=30)

    # Predicted first-hop range bee1 -> Poker Flat at h=300 km.
    gc = qa.haversine_km(_RX_LAT, _RX_LON, *_TX_POKER)
    expected = qa.expected_first_hop_range_km(gc)

    records: list[dict] = []
    # Many Poker Flat detections clustered at expected range bin.
    for i in range(100):
        records.append(_record(
            tx_id="POKER_FLAT",
            freq=2_900_000,
            t=base + timedelta(seconds=18 * i),
            # Tight cluster within ±50 km of expected (well inside ±200).
            pseudorange_km=expected + ((i % 10) - 5) * 10.0,
        ))
    # A few Palmer false-positives uniformly spread (null floor).
    for i in range(10):
        records.append(_record(
            tx_id="PALMER",
            freq=2_900_000,
            t=base + timedelta(seconds=180 * i),
            pseudorange_km=200.0 + i * 1400.0,
        ))
    _spool(data_root, "AC0G-B1", records)

    result = qa.run_qa(
        instance="AC0G-B1",
        since=timedelta(hours=1),
        data_root=data_root,
        etc_root=etc,
        stations_path=etc / "data" / "stations.toml",
        now=now,
    )

    assert result.error is None, result.error
    poker_29 = [r for r in result.rows
                if r["tx_id"] == "POKER_FLAT" and r["frequency_hz"] == 2_900_000]
    assert len(poker_29) == 1
    row = poker_29[0]
    assert row["verdict"] == "SIGNAL", row
    assert row["vs_palmer"] is not None and row["vs_palmer"] >= 3.0
    assert row["pct_in_bin"] >= 0.30


def test_qa_no_data_in_window_reports_clear_error(tmp_path: Path) -> None:
    etc, data_root, now = _common_setup(tmp_path)
    # No spool written.
    result = qa.run_qa(
        instance="AC0G-B1",
        since=timedelta(hours=1),
        data_root=data_root,
        etc_root=etc,
        stations_path=etc / "data" / "stations.toml",
        now=now,
    )
    assert result.error is not None
    assert "no records found" in result.error


def test_qa_codeless_only_yields_helpful_error(tmp_path: Path) -> None:
    """If the daemon emitted codeless records but not locked records in
    the window, qa should refuse with a message naming the cause."""
    etc, data_root, now = _common_setup(tmp_path)
    # Write a codeless record so has_codeless_records() returns True.
    cl_path = (data_root / "AC0G-B1" / "codeless"
               / f"{now.year:04d}" / f"{now.month:02d}"
               / f"{now.day:02d}.jsonl")
    cl_path.parent.mkdir(parents=True, exist_ok=True)
    cl_path.write_text(
        json.dumps({
            "time": now.isoformat(),
            "mode": "codeless",
            "frequency_hz": 2_900_000,
            "autocorr_db": 5.0,
        }) + "\n"
    )
    result = qa.run_qa(
        instance="AC0G-B1",
        since=timedelta(hours=1),
        data_root=data_root,
        etc_root=etc,
        stations_path=etc / "data" / "stations.toml",
        now=now,
    )
    assert result.error is not None
    assert "codeless" in result.error
    assert "Palmer" in result.error


def test_qa_aggregate_rolls_up_frequencies(tmp_path: Path) -> None:
    etc, data_root, now = _common_setup(tmp_path)
    base = now - timedelta(minutes=30)
    records: list[dict] = []
    # 20 Poker records per freq → 40 total in aggregate.
    for freq in (2_900_000, 3_400_000):
        for i in range(20):
            records.append(_record(
                tx_id="POKER_FLAT", freq=freq,
                t=base + timedelta(seconds=60 * i),
                pseudorange_km=5000.0,
            ))
    # Palmer null floor.
    for i in range(5):
        records.append(_record(
            tx_id="PALMER", freq=2_900_000,
            t=base + timedelta(seconds=360 * i),
            pseudorange_km=200.0 + i * 1400.0,
        ))
    _spool(data_root, "AC0G-B1", records)

    result = qa.run_qa(
        instance="AC0G-B1",
        since=timedelta(hours=1),
        aggregate=True,
        data_root=data_root,
        etc_root=etc,
        stations_path=etc / "data" / "stations.toml",
        now=now,
    )
    assert result.error is None, result.error
    # In aggregate mode, one row per Tx.
    poker_rows = [r for r in result.rows if r["tx_id"] == "POKER_FLAT"]
    assert len(poker_rows) == 1, poker_rows
    assert poker_rows[0]["n_detections"] == 40


def test_qa_palmer_zero_detections_falls_back_to_in_bin_only(tmp_path: Path) -> None:
    """When Palmer has zero false-positives in the window, vs_palmer is
    undefined; the verdict must rely on the in-bin check alone."""
    etc, data_root, now = _common_setup(tmp_path)
    base = now - timedelta(minutes=30)
    records: list[dict] = []
    # Strong, well-clustered Poker signal; NO Palmer records at all.
    gc = qa.haversine_km(_RX_LAT, _RX_LON, *_TX_POKER)
    expected = qa.expected_first_hop_range_km(gc)
    for i in range(50):
        records.append(_record(
            tx_id="POKER_FLAT", freq=2_900_000,
            t=base + timedelta(seconds=36 * i),
            pseudorange_km=expected + ((i % 5) - 2) * 20.0,
        ))
    _spool(data_root, "AC0G-B1", records)

    result = qa.run_qa(
        instance="AC0G-B1",
        since=timedelta(hours=1),
        data_root=data_root,
        etc_root=etc,
        stations_path=etc / "data" / "stations.toml",
        now=now,
    )
    poker_29 = [r for r in result.rows
                if r["tx_id"] == "POKER_FLAT" and r["frequency_hz"] == 2_900_000]
    assert poker_29[0]["vs_palmer"] is None
    # in-bin alone meets WEAK_PCT_IN_BIN; gets WEAK (not SIGNAL, since
    # vs_palmer is undefined).
    assert poker_29[0]["verdict"] in {"WEAK", "NULL"}
    # in-bin pct is high → should be at least WEAK.
    assert poker_29[0]["verdict"] == "WEAK"


def test_qa_emits_zero_detection_rows_for_quiet_tx(tmp_path: Path) -> None:
    """Tx in [transmitters].enabled but with no detections in window
    must still appear as a row (with n_detections=0) — otherwise the
    operator can't tell a quiet Tx apart from a misconfigured one."""
    etc, data_root, now = _common_setup(tmp_path)
    base = now - timedelta(minutes=30)
    # Only POKER, no GAKONA, no PALMER.
    records = [_record(
        tx_id="POKER_FLAT", freq=2_900_000,
        t=base, pseudorange_km=200.0,
    )]
    _spool(data_root, "AC0G-B1", records)

    result = qa.run_qa(
        instance="AC0G-B1",
        since=timedelta(hours=1),
        data_root=data_root,
        etc_root=etc,
        stations_path=etc / "data" / "stations.toml",
        now=now,
    )
    tx_ids = {r["tx_id"] for r in result.rows}
    assert {"POKER_FLAT", "GAKONA", "PALMER"} <= tx_ids


def test_format_human_table_includes_header_and_thresholds(tmp_path: Path) -> None:
    etc, data_root, now = _common_setup(tmp_path)
    records = [_record(
        tx_id="POKER_FLAT", freq=2_900_000,
        t=now - timedelta(minutes=10), pseudorange_km=200.0,
    )]
    _spool(data_root, "AC0G-B1", records)
    result = qa.run_qa(
        instance="AC0G-B1",
        since=timedelta(hours=1),
        data_root=data_root,
        etc_root=etc,
        stations_path=etc / "data" / "stations.toml",
        now=now,
    )
    out = qa.format_human(result)
    assert "tx_id" in out and "verdict" in out  # header rendered
    assert "POKER_FLAT" in out
    assert "Thresholds" in out
    assert "SIGNAL" in out and "WEAK" in out
