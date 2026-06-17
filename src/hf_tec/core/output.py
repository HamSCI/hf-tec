"""Output sinks: daily-rotated JSONL + additive HamSCI SQLite sink.

The JSONL writer is the canonical L1 artefact.  The HamSCI sink writer
is additive (CONTRACT v0.6 §17) — silent no-op when sigmond's
SQLite sink at /var/lib/sigmond/sink.db is unwritable.

Future: an opt-in `.out.mod` text writer for direct Hysell `focus.c`
inversion compatibility.  Scaffolded as a stub here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import __version__
from ..config import Config
from ..contract import CONTRACT_VERSION
from .detect import Detection
from .detect_codeless import CodelessDetection


logger = logging.getLogger(__name__)


DEFAULT_DATA_ROOT = Path("/var/lib/hf-tec")


class OutputSink:
    """Aggregator for every enabled output mode.

    Two record types flow through this sink, each with its own JSONL
    stream and HamSCI sink table:

    - **locked** — full PRN-correlator detections with per-Tx
      pseudorange.  Spool: ``<data_root>/<radiod>/locked/``.  Sink
      table: ``hf_tec.spots``.
    - **codeless** — code-free autocorrelation detections (no Tx ID,
      no pseudorange).  Spool: ``<data_root>/<radiod>/codeless/``.
      Sink table: ``hf_tec_codeless.spots``.
    """

    def __init__(self, cfg: Config, instance: str, data_root: Optional[Path] = None):
        self.cfg = cfg
        self.instance = instance                # per-instance state dir
        self.data_root = data_root or DEFAULT_DATA_ROOT
        # Phase v0.8 §19.3: reporter_id stamps every emitted spot row.
        # Source: [instance].reporter_id from config if present; otherwise
        # fall back to the systemd instance name (which IS the reporter_id
        # under MULTI-INSTANCE-ARCHITECTURE.md §3 — they coincide).
        self.reporter_id = cfg.instance.reporter_id or instance

        self._locked_jsonl: Optional[_JsonlWriter] = None
        self._codeless_jsonl: Optional[_JsonlWriter] = None
        self._locked_sink: Optional[_HamsciSinkWriter] = None
        self._codeless_sink: Optional[_HamsciSinkWriter] = None
        if cfg.sinks.local_jsonl:
            self._locked_jsonl = _JsonlWriter(self.data_root / instance / "locked")
            self._codeless_jsonl = _JsonlWriter(self.data_root / instance / "codeless")
        if cfg.sinks.hamsci_sink:
            self._locked_sink = _HamsciSinkWriter(table="hf_tec.spots")
            self._codeless_sink = _HamsciSinkWriter(table="hf_tec_codeless.spots")

    def write_detection(
        self,
        *,
        tx_id: str,
        rx_id: str,
        radiod_id: str,
        frequency_hz: int,
        timestamp_utc: datetime,
        detection: Detection,
    ) -> None:
        record = {
            "time": timestamp_utc.replace(tzinfo=timezone.utc).isoformat(),
            "mode": "locked",
            "reporter_id": self.reporter_id,
            "tx_id": tx_id,
            "rx_id": rx_id,
            "radiod_id": radiod_id,
            "frequency_hz": int(frequency_hz),
            "pseudorange_km": detection.pseudorange_km,
            "doppler_hz": detection.doppler_hz,
            "amplitude_db": detection.amplitude_db,
            "snr_db": detection.snr_db,
            "noise_floor_db": detection.noise_floor_db,
            "lock_quality": detection.lock_quality,
            "range_bin": detection.range_bin,
            "n_hops": 1,
            "processing_version": __version__,
            "contract_version": CONTRACT_VERSION,
        }
        if self._locked_jsonl is not None:
            self._locked_jsonl.write(timestamp_utc, record)
        if self._locked_sink is not None:
            self._locked_sink.write(record)

    def write_codeless_detection(
        self,
        *,
        rx_id: str,
        radiod_id: str,
        frequency_hz: int,
        timestamp_utc: datetime,
        detection: CodelessDetection,
    ) -> None:
        record = {
            "time": timestamp_utc.replace(tzinfo=timezone.utc).isoformat(),
            "mode": "codeless",
            "reporter_id": self.reporter_id,
            "rx_id": rx_id,
            "radiod_id": radiod_id,
            "frequency_hz": int(frequency_hz),
            "integration_seconds": detection.integration_seconds,
            "n_samples": detection.n_samples,
            "autocorr_magnitude": detection.autocorr_magnitude,
            "autocorr_floor": detection.autocorr_floor,
            "autocorr_db": detection.autocorr_db,
            "autocorr_phase_rad": detection.autocorr_phase_rad,
            "doppler_hz": detection.doppler_hz,
            "snr_estimate_db": detection.snr_estimate_db,
            "band_power_db": detection.band_power_db,
            "detection": detection.detection,
            "detection_threshold_db": detection.detection_threshold_db,
            "processing_version": __version__,
            "contract_version": CONTRACT_VERSION,
        }
        if self._codeless_jsonl is not None:
            self._codeless_jsonl.write(timestamp_utc, record)
        if self._codeless_sink is not None:
            self._codeless_sink.write(record)

    def close(self) -> None:
        for writer in (
            self._locked_jsonl,
            self._codeless_jsonl,
            self._locked_sink,
            self._codeless_sink,
        ):
            if writer is not None:
                writer.close()


# ---------------------------------------------------------------------------
# JSONL writer
# ---------------------------------------------------------------------------


class _JsonlWriter:
    """Daily-rotated JSONL writer.  One file per UTC day."""

    def __init__(self, root: Path):
        self.root = root
        self._current_path: Optional[Path] = None
        self._fh = None

    def write(self, ts: datetime, record: dict) -> None:
        path = self._path_for(ts)
        if path != self._current_path:
            self._reopen(path)
        line = json.dumps(record, separators=(",", ":"))
        assert self._fh is not None  # noqa: S101
        self._fh.write(line + "\n")
        self._fh.flush()

    def _path_for(self, ts: datetime) -> Path:
        return (
            self.root
            / f"{ts.year:04d}"
            / f"{ts.month:02d}"
            / f"{ts.day:02d}.jsonl"
        )

    def _reopen(self, path: Path) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                logger.exception("close of previous JSONL file failed")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")  # noqa: SIM115
        self._current_path = path

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                logger.exception("close of JSONL file failed")
        self._fh = None
        self._current_path = None


# ---------------------------------------------------------------------------
# HamSCI sink writer (CONTRACT v0.6 §17)
# ---------------------------------------------------------------------------


class _HamsciSinkWriter:
    """Inserts rows into a named HamSCI sink table via
    ``sigmond.hamsci_sink.Writer``.

    Lazy-imports `sigmond.hamsci_sink` so the package remains usable
    when sigmond isn't installed (silent no-op).
    """

    def __init__(self, table: str = "hf_tec.spots") -> None:
        self._writer = None
        self._table = table
        try:
            from sigmond.hamsci_sink import Writer  # type: ignore[import-not-found]
        except Exception:
            logger.info("sigmond.hamsci_sink not available; %s disabled", table)
            return
        try:
            self._writer = Writer.from_env(table=table)
        except Exception:
            logger.exception("HamSCI sink Writer (%s) construction failed; disabled", table)
            self._writer = None

    def write(self, record: dict) -> None:
        if self._writer is None:
            return
        try:
            self._writer.insert(record)
        except Exception:
            # Don't let sink errors stop the daemon — the JSONL is canonical.
            logger.exception("HamSCI sink insert failed")

    def close(self) -> None:
        if self._writer is None:
            return
        try:
            close = getattr(self._writer, "close", None)
            if callable(close):
                close()
        except Exception:
            logger.exception("HamSCI sink close failed")
        self._writer = None
