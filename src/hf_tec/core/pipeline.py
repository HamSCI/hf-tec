"""FreqPipeline — per-frequency orchestrator.

Owns: I/Q source for one Tx frequency, replica bank for every enabled
Tx at that frequency, coherent + incoherent integrators (one per Tx),
first-hop detector, output sink.

Per Hysell 2018 §2 the per-minute record is the lowest-cadence
artefact.  The pipeline emits one record per (Tx, freq) per minute
after six successive 10-s coherent windows are incoherently averaged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..config import Config, FrequencyConfig
from ..stations import Station, StationDb
from .coherent import CoherentStack, IncoherentAccumulator
from .correlate import ReplicaBank, correlate_bank
from .detect import Detection, first_hop_detection
from .output import OutputSink
from .stream import IqFrame, IqFrameSource


logger = logging.getLogger(__name__)


@dataclass
class FreqPipeline:
    cfg: Config
    freq_cfg: FrequencyConfig
    stations: StationDb
    source: IqFrameSource
    sink: OutputSink
    radiod_id: str
    rx_station_id: str

    _bank: ReplicaBank = field(init=False, repr=False)
    _coherent: dict[str, CoherentStack] = field(default_factory=dict, init=False, repr=False)
    _incoherent: dict[str, IncoherentAccumulator] = field(default_factory=dict, init=False, repr=False)
    _last_frame_ts: Optional[object] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        proc = self.cfg.processing
        samples_per_frame = self.freq_cfg.sample_rate_hz * proc.code_period_ms // 1000
        samples_per_chip = samples_per_frame // proc.code_chips
        if samples_per_chip < 1:
            raise ValueError(
                f"sample_rate {self.freq_cfg.sample_rate_hz} is too low for "
                f"chip_us={proc.chip_microseconds}, code_chips={proc.code_chips}"
            )

        self._bank = ReplicaBank(
            n_samples=samples_per_frame,
            samples_per_chip=samples_per_chip,
        )
        enabled_tx = self._enabled_transmitters_at_freq()
        wired_tx: list[Station] = []
        for tx in enabled_tx:
            if tx.prn_seed is None:
                logger.warning(
                    "FreqPipeline[%d Hz]: skipping transmitter %s "
                    "(no prn_seed in stations DB) — add `prn_seed = N` "
                    "to its [transmitters.%s] block before enabling.",
                    self.freq_cfg.center_hz, tx.site_id, tx.site_id,
                )
                continue
            self._bank.add(tx.site_id, self.freq_cfg.center_hz, prn_seed=tx.prn_seed)
            self._coherent[tx.site_id] = CoherentStack(
                n_reps=proc.coherent_reps,
                n_range_bins=proc.code_chips,
            )
            self._incoherent[tx.site_id] = IncoherentAccumulator(
                n_windows=proc.incoherent_windows,
            )
            wired_tx.append(tx)
        logger.info(
            "FreqPipeline[%d Hz] ready: %d transmitter(s): %s",
            self.freq_cfg.center_hz, len(wired_tx),
            ", ".join(t.site_id for t in wired_tx),
        )

    def _enabled_transmitters_at_freq(self) -> list[Station]:
        out: list[Station] = []
        for tx_id in self.cfg.transmitters_enabled:
            tx = self.stations.transmitter(tx_id)
            if tx is None:
                continue
            if self.freq_cfg.center_hz in tx.frequencies_hz:
                out.append(tx)
        return out

    def process_frame(self, frame: IqFrame) -> None:
        """Consume one code-period I/Q frame; emit records when full."""
        if frame.frequency_hz != self.freq_cfg.center_hz:
            return
        self._last_frame_ts = frame.timestamp_utc

        # Correlate against every replica at once.  Range profiles are
        # complex, one bin per chip (= one range cell).
        profiles = correlate_bank(frame.samples, self._bank)

        for tx_id, profile in profiles.items():
            stack = self._coherent[tx_id]
            full = stack.push(profile)
            if not full:
                continue
            # Coherent window complete → range-Doppler matrix.
            rd = stack.range_doppler()
            stack.reset()

            acc = self._incoherent[tx_id]
            done = acc.push(rd)
            if not done:
                continue
            # Incoherent average complete → emit one record.
            power = acc.average()
            acc.reset()

            self._emit(tx_id, frame, power)

    def _emit(self, tx_id: str, frame: IqFrame, power_matrix) -> None:
        proc = self.cfg.processing
        det: Optional[Detection] = first_hop_detection(
            power_matrix,
            chip_microseconds=float(proc.chip_microseconds),
            code_period_s=proc.code_period_ms / 1000.0,
            snr_threshold_db=proc.snr_threshold_db,
            min_pseudorange_km=proc.min_pseudorange_km,
            max_pseudorange_km=proc.max_pseudorange_km,
        )
        if det is None:
            logger.debug("tx=%s freq=%d: no first-hop above SNR gate",
                         tx_id, self.freq_cfg.center_hz)
            return
        self.sink.write_detection(
            tx_id=tx_id,
            rx_id=self.rx_station_id,
            radiod_id=self.radiod_id,
            frequency_hz=self.freq_cfg.center_hz,
            timestamp_utc=frame.timestamp_utc,
            detection=det,
        )

    def close(self) -> None:
        try:
            self.source.close()
        except Exception:
            logger.exception("source close failed")
