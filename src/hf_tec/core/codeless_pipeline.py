"""CodelessPipeline — per-frequency orchestrator for code-free detection.

Consumes IqFrames from one Tx-frequency channel, accumulates them into
an integration-window buffer (default 60 s), and runs the codeless
detector at the end of each window.  Emits one record per minute per
frequency, written to a separate output stream so the schema stays
clean from the locked-mode pipeline's records.

Selected by the daemon when the recorder is in code-free mode — by
default whenever the PRN code generator is still a stub (the recorder
auto-falls-back to this pipeline so an operator running the
scaffolding gets immediately-useful first-light data rather than
locked-mode records derived from a fake code).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..config import Config, FrequencyConfig
from .detect_codeless import CodelessDetection, codeless_detect
from .output import OutputSink
from .stream import IqFrame, IqFrameSource


logger = logging.getLogger(__name__)


@dataclass
class CodelessPipeline:
    cfg: Config
    freq_cfg: FrequencyConfig
    source: IqFrameSource
    sink: OutputSink
    radiod_id: str
    rx_station_id: str

    _frames: list[np.ndarray] = field(default_factory=list, init=False, repr=False)
    _frames_target: int = field(default=0, init=False, repr=False)
    _first_ts: object = field(default=None, init=False, repr=False)
    _last_ts: object = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        proc = self.cfg.processing
        frames_per_second = 1000.0 / proc.code_period_ms
        self._frames_target = int(round(proc.codeless_integration_seconds * frames_per_second))
        if self._frames_target < 2:
            raise ValueError(
                f"codeless_integration_seconds={proc.codeless_integration_seconds} "
                f"× {frames_per_second} fps = {self._frames_target} frames; "
                f"need at least 2"
            )
        logger.info(
            "CodelessPipeline[%d Hz] ready: integration=%.1f s (%d frames), "
            "threshold=%.1f dB",
            self.freq_cfg.center_hz,
            proc.codeless_integration_seconds,
            self._frames_target,
            proc.codeless_detection_threshold_db,
        )

    def process_frame(self, frame: IqFrame) -> None:
        """Consume one code-period frame; emit a record when the window fills."""
        if frame.frequency_hz != self.freq_cfg.center_hz:
            return
        if not self._frames:
            self._first_ts = frame.timestamp_utc
        self._last_ts = frame.timestamp_utc
        self._frames.append(frame.samples)

        if len(self._frames) >= self._frames_target:
            self._emit()
            self._frames.clear()
            self._first_ts = None
            self._last_ts = None

    def _emit(self) -> None:
        buffer = np.concatenate(self._frames).astype(np.complex64, copy=False)
        proc = self.cfg.processing
        detection: Optional[CodelessDetection] = codeless_detect(
            buffer,
            sample_rate_hz=self.freq_cfg.sample_rate_hz,
            code_period_s=proc.code_period_ms / 1000.0,
            detection_threshold_db=proc.codeless_detection_threshold_db,
        )
        if detection is None:
            logger.warning(
                "codeless detector returned None at %d Hz — buffer too short?",
                self.freq_cfg.center_hz,
            )
            return
        # Use the timestamp at the *end* of the integration window so
        # the record's time field matches the locked-mode convention.
        ts = self._last_ts if self._last_ts is not None else self._first_ts
        self.sink.write_codeless_detection(
            rx_id=self.rx_station_id,
            radiod_id=self.radiod_id,
            frequency_hz=self.freq_cfg.center_hz,
            timestamp_utc=ts,
            detection=detection,
        )
        logger.info(
            "codeless @ %d Hz: autocorr=%.1f dB %s  doppler=%.3f Hz  "
            "snr≈%.1f dB  band_pwr=%.1f dB",
            self.freq_cfg.center_hz,
            detection.autocorr_db,
            "DETECT" if detection.detection else "—",
            detection.doppler_hz,
            detection.snr_estimate_db,
            detection.band_power_db,
        )

    def close(self) -> None:
        try:
            self.source.close()
        except Exception:
            logger.exception("source close failed")
