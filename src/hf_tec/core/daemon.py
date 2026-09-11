"""HfTecRecorder — top-level daemon orchestrator.

Spawns one FreqPipeline per enabled frequency, each subscribing to
its own ka9q-radio channel.  Manages lifecycle (start, graceful stop,
backoff restart on per-pipeline failure).  Integrates with systemd
via sd_notify for Type=notify readiness signalling.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import Config, FrequencyConfig
from ..stations import StationDb, load_stations
from .applied_state import applied_state_for, applied_state_path, instance_name
from .codeless_pipeline import CodelessPipeline
from .output import DEFAULT_DATA_ROOT, OutputSink
from .pipeline import FreqPipeline
from .stream import HfTecSource


logger = logging.getLogger(__name__)

# How often the daemon refreshes its applied-state file.  read_applied_state
# treats a file older than 300 s as "nothing running".
APPLIED_STATE_PERIOD_S = 60.0


# ---------------------------------------------------------------------------
# sd_notify — minimal implementation (no python-systemd dependency).
# ---------------------------------------------------------------------------


def _sd_notify(message: str) -> None:
    socket_path = os.environ.get("NOTIFY_SOCKET")
    if not socket_path:
        return
    if socket_path.startswith("@"):
        # Abstract socket — replace with NUL prefix.
        socket_path = "\0" + socket_path[1:]
    try:
        import socket as _s
        with _s.socket(_s.AF_UNIX, _s.SOCK_DGRAM) as sock:
            sock.connect(socket_path)
            sock.sendall(message.encode("utf-8"))
    except OSError:
        logger.debug("sd_notify failed (NOTIFY_SOCKET=%s)", socket_path)


# ---------------------------------------------------------------------------
# Per-pipeline worker thread with exponential-backoff restart.
# ---------------------------------------------------------------------------


@dataclass
class _PipelineWorker:
    pipeline_factory: object   # callable returning FreqPipeline
    name: str
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    backoff_s: float = 2.0
    # The pipeline now running, None while the worker waits out a backoff.
    # The daemon reads ``pipeline.source.anchor`` from it for the §3 report.
    pipeline: Optional[object] = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            pipeline: Optional[FreqPipeline] = None
            crashed = False
            try:
                pipeline = self.pipeline_factory()  # type: ignore[assignment]
                self.pipeline = pipeline
                logger.info("[%s] pipeline running", self.name)
                self.backoff_s = 2.0  # reset on successful start
                for frame in pipeline.source.frames():
                    if self.stop_event.is_set():
                        break
                    pipeline.process_frame(frame)
            except Exception:
                crashed = True
                logger.exception("[%s] pipeline crashed", self.name)
            finally:
                # Always release the source (RadiodStream RX thread + sample
                # queue) before retry/exit, so a crashed-then-restarted
                # pipeline never orphans its prior RTP subscription.  Before
                # this, close() ran only on clean exhaustion, leaking one
                # stream + RX thread per crash-restart.
                self.pipeline = None
                if pipeline is not None:
                    try:
                        pipeline.close()
                    except Exception:
                        logger.exception("[%s] pipeline close failed", self.name)
            if not crashed:
                # Clean source exhaustion — uncommon for live capture.
                logger.info("[%s] source exhausted; worker exiting", self.name)
                return
            # Exponential backoff up to 60 s.
            wait = min(self.backoff_s, 60.0)
            logger.warning("[%s] restarting in %.1f s", self.name, wait)
            if self.stop_event.wait(wait):
                break
            self.backoff_s = min(self.backoff_s * 2.0, 60.0)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=10.0)


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


@dataclass
class HfTecRecorder:
    cfg: Config
    instance: str             # = reporter_id ≡ systemd @<i>; also output-path dir
    stations: Optional[StationDb] = None
    data_root: Optional[Path] = None   # None -> DEFAULT_DATA_ROOT
    _workers: list[_PipelineWorker] = field(default_factory=list, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _applied_state_written_at: Optional[float] = field(default=None, init=False)

    @property
    def radiod_id(self) -> str:
        """Identifier of the radiod that served the IQ — used for the
        `radiod_id` field of every emitted record.  Derived from the
        ka9q status DNS address (canonical sigmond convention)."""
        return self.cfg.ka9q.status_address or self.instance

    def run(self) -> int:
        if self.stations is None:
            self.stations = load_stations()

        # systemd always passes --instance %i, but a manual `hf-tec daemon`
        # may not.  A None/empty instance would otherwise blow up deep inside
        # OutputSink's path construction with an opaque TypeError.  The
        # fallback rule lives in applied_state.instance_name so inventory
        # (another process) resolves the same directory.
        self._resolve_instance()
        if not self.instance:
            logger.error(
                "no instance name: pass --instance, or set [instance] "
                "reporter_id or [ka9q] status_address in the config"
            )
            return 2

        rx_id = self.cfg.station.station_id
        sink = OutputSink(self.cfg, instance=self.instance, data_root=self.data_root)

        # One worker per enabled frequency.
        enabled = [f for f in self.cfg.frequencies if f.enabled]
        if not enabled:
            logger.error("no enabled frequencies; nothing to do")
            return 2
        for f in enabled:
            worker = _PipelineWorker(
                pipeline_factory=lambda f=f: self._build_pipeline(f, sink, rx_id),
                name=f"freq-{f.center_hz//1000}kHz",
            )
            self._workers.append(worker)
        for w in self._workers:
            w.start()

        # Install signal handlers.
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())

        resolved_mode = self.cfg.resolved_mode()
        _sd_notify(f"READY=1\nSTATUS=hf-tec running ({resolved_mode} mode)")
        logger.info(
            "daemon ready: radiod=%s instance=%s frequencies=%s mode=%s",
            self.radiod_id, self.instance,
            [f.center_hz for f in enabled],
            resolved_mode,
        )

        # Health watchdog tick.  systemd's WatchdogSec= will use this if set.
        watchdog_us = int(os.environ.get("WATCHDOG_USEC", "0"))
        watchdog_s = watchdog_us / 1e6 if watchdog_us > 0 else 30.0

        try:
            while not self._stop.is_set():
                self._maybe_write_applied_state()
                self._stop.wait(timeout=watchdog_s / 2)
                _sd_notify("WATCHDOG=1")
        finally:
            self._shutdown(sink)
        return 0

    # ---- §3 timing_authority_applied report ----------------------------------

    def _resolve_instance(self) -> None:
        if not self.instance:
            self.instance = instance_name(self.cfg) or ""

    def _applied_state_path(self) -> Path:
        return applied_state_path(self.data_root or DEFAULT_DATA_ROOT, self.instance)

    def _maybe_write_applied_state(self, now: Optional[float] = None) -> None:
        """Write at most once per minute.  The heartbeat loop wakes every
        watchdog_s/2 seconds, which can run well under a minute."""
        now = time.monotonic() if now is None else now
        last = self._applied_state_written_at
        if last is not None and now - last < APPLIED_STATE_PERIOD_S:
            return
        self._applied_state_written_at = now
        self._write_applied_state()

    def _write_applied_state(self) -> None:
        """Leave the §3 ``timing_authority_applied`` block at
        ``<data_root>/<instance>/timing-authority.json`` for ``inventory
        --json`` (another process) to report.  See core/applied_state.py.
        Best-effort: the report must never take the daemon down."""
        from hamsci_dsp.timing import write_applied_state
        try:
            write_applied_state(
                self._applied_state_path(),
                applied_state_for(self._workers, client_radiod=self.radiod_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("applied-state write failed: %s", exc)

    def _build_pipeline(
        self, freq_cfg: FrequencyConfig, sink: OutputSink, rx_id: str
    ):
        """Build either a FreqPipeline (locked mode) or CodelessPipeline,
        based on the resolved operating mode."""
        source = HfTecSource(
            radiod_status=self.cfg.ka9q.status_address,
            frequency_hz=freq_cfg.center_hz,
            sample_rate_hz=freq_cfg.sample_rate_hz,
            filter_guard_hz=self.cfg.ka9q.filter_guard_hz,
            frame_n_samples=(
                freq_cfg.sample_rate_hz * self.cfg.processing.code_period_ms // 1000
            ),
            radiod_id=self.radiod_id,
            stall_timeout_s=self.cfg.ka9q.stall_timeout_s,
        )
        if self.cfg.resolved_mode() == "codeless":
            return CodelessPipeline(
                cfg=self.cfg,
                freq_cfg=freq_cfg,
                source=source,
                sink=sink,
                radiod_id=self.radiod_id,
                rx_station_id=rx_id,
            )
        assert self.stations is not None  # noqa: S101
        return FreqPipeline(
            cfg=self.cfg,
            freq_cfg=freq_cfg,
            stations=self.stations,
            source=source,
            sink=sink,
            radiod_id=self.radiod_id,
            rx_station_id=rx_id,
        )

    def _shutdown(self, sink: OutputSink) -> None:
        _sd_notify("STOPPING=1")
        logger.info("daemon shutting down")
        for w in self._workers:
            w.stop()
        sink.close()
        logger.info("daemon stopped")
