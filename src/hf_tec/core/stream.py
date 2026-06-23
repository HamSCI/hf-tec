"""HfTecSource — wideband I/Q ingest from radiod via ka9q-python.

Subscribes to one ka9q-radio channel per Tx frequency.  Provisioned
dynamically through ``RadiodControl.ensure_channel`` so radiod does
not need a static channel fragment — adding a frequency to the
recorder config is sufficient.  Frames are emitted at the code-period
boundary (10,000 samples at 100 kS/s = 100 ms by default),
timestamped off the GPSDO-clocked RTP counter: the first sample's UTC
is derived once via ka9q rtp_to_wallclock + the hf-timestd authority
offset, then every frame's label is pure sample-count projection
(METROLOGY.md §4.5 RTP-reference invariant).  The host wall clock is
used only as a loudly-warned fallback when no RTP timing is available.

ka9q-python is the only mandatory runtime dependency for live
capture; it is lazy-imported so the rest of the package (config,
contract, tests) remains usable without it.

Implementation follows the codar-sounder reference: provision via
``RadiodControl.ensure_channel(encoding=4, ...)`` (F32LE) to avoid an
S16BE byte-swap pathology in the ka9q-python / radiod combination
deployed on the suite hosts; sample-sanitise NaN / overflow values
produced by the resequencer's gap-fill regions to zero so they
cannot poison the autocorrelation downstream.
"""

from __future__ import annotations

import logging
import queue as _q
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional, Protocol

import numpy as np


logger = logging.getLogger(__name__)


class SourceStalled(RuntimeError):
    """Raised by ``frames()`` when no I/Q has arrived for longer than the
    configured stall timeout.  Propagates up to the pipeline worker, which
    closes the source and re-subscribes on its exponential-backoff path —
    turning an otherwise-silent radiod stall into a self-healing restart."""


@dataclass(frozen=True)
class IqFrame:
    """One code-period frame of complex I/Q with UTC anchor."""
    frequency_hz: int
    sample_rate_hz: int
    samples: np.ndarray            # complex64, shape (n_samples,)
    timestamp_utc: datetime
    rtp_anchor_ns: Optional[int] = None
    radiod_id: str = ""
    # Cumulative samples dropped (queue overflow) before this frame's first
    # sample.  The timestamp already accounts for the gap, so this is purely
    # provenance: a change in this value between consecutive frames marks a
    # discontinuity downstream consumers may wish to flag.
    dropped_samples_before: int = 0


class IqFrameSource(Protocol):
    """Anything that yields IqFrames for one frequency."""

    def frames(self) -> Iterator[IqFrame]: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Live source — ka9q-python RadiodControl + RadiodStream
# ---------------------------------------------------------------------------


@dataclass
class HfTecSource:
    """Live RTP I/Q source for one Tx frequency."""
    radiod_status: str            # mDNS hostname of the radiod
    frequency_hz: int
    sample_rate_hz: int = 100_000
    filter_guard_hz: int = 1500
    frame_n_samples: int = 10_000
    client_id: str = "hf-tec"
    radiod_id: str = ""
    preset: str = "iq"
    # Raise SourceStalled if no samples arrive for this many seconds.  Frames
    # flow every code period (100 ms) whenever radiod is alive — independent
    # of whether any beacon is being detected — so a multi-second silence is
    # a dead stream, not merely a quiet band.  0 disables the watchdog.
    stall_timeout_s: float = 30.0

    _control: object = field(default=None, init=False, repr=False)
    _stream: object = field(default=None, init=False, repr=False)
    _channel_info: object = field(default=None, init=False, repr=False)
    _sample_queue: object = field(default=None, init=False, repr=False)
    _stopped: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _anchor_first_rtp: Optional[int] = field(default=None, init=False, repr=False)
    # RTP-reference timing: the UTC of the first sample, derived ONCE from
    # rtp_to_wallclock + the hf-timestd authority offset; every frame label
    # then projects off it by sample count.  _authority_reader is injectable
    # for tests; defaults to the real /run/hf-timestd/authority.json reader.
    _authority_reader: object = field(default=None, init=False, repr=False)
    _anchor_utc: Optional[datetime] = field(default=None, init=False, repr=False)
    _frame_index: int = field(default=0, init=False, repr=False)
    # Cumulative samples dropped on queue overflow (RX thread writes, frame
    # iterator reads).  Folded into the sample-count timestamp projection so a
    # backlog advances the timeline by the real gap instead of mislabelling
    # subsequent frames as contiguous.
    _dropped_samples: int = field(default=0, init=False, repr=False)

    # ---- ka9q lazy import ---------------------------------------------------

    @staticmethod
    def _import_ka9q():
        from ka9q.control import RadiodControl    # type: ignore[import-not-found]
        from ka9q.stream import RadiodStream      # type: ignore[import-not-found]
        return RadiodControl, RadiodStream

    # ---- lifecycle ----------------------------------------------------------

    def open(self) -> None:
        if self._stream is not None:
            return
        RadiodControl, RadiodStream = self._import_ka9q()

        nyquist = self.sample_rate_hz // 2
        low_edge = -(nyquist - self.filter_guard_hz)
        high_edge = +(nyquist - self.filter_guard_hz)

        logger.info(
            "opening ka9q channel: status=%s freq=%d sr=%d filter=[%+d,%+d] Hz "
            "preset=%s encoding=F32LE",
            self.radiod_status, self.frequency_hz, self.sample_rate_hz,
            low_edge, high_edge, self.preset,
        )

        # client_id makes ka9q-python derive a per-(client, radiod)
        # multicast destination so this stream doesn't share a multicast
        # group with peer clients on the same radiod.  CONTRACT v0.3 §7 /
        # ka9q-python ≥ 3.14.0.
        self._control = RadiodControl(
            self.radiod_status,
            client_id=self.client_id,
        )
        # Force F32LE (encoding=4) IQ.  ka9q-python's default S16BE
        # delivers byte-swap-corrupted samples on these hosts
        # (codar-sounder learned this the hard way).
        self._channel_info = self._control.ensure_channel(
            frequency_hz=float(self.frequency_hz),
            preset=self.preset,
            sample_rate=int(self.sample_rate_hz),
            encoding=4,                # F32LE
            low_edge=float(low_edge),
            high_edge=float(high_edge),
        )
        logger.info(
            "channel ready: ssrc=%s mcast=%s:%d",
            getattr(self._channel_info, "ssrc", "?"),
            getattr(self._channel_info, "multicast_address", "?"),
            getattr(self._channel_info, "port", 0),
        )

        # Bounded queue.  ka9q delivers ~30 ms batches; 64 entries ≈ 2 s
        # of buffering — enough for jitter, not enough to mask a real
        # backlog.
        self._sample_queue = _q.Queue(maxsize=64)
        self._stream = RadiodStream(
            channel=self._channel_info,
            on_samples=self._on_samples,
        )
        self._stream.start()

    def close(self) -> None:
        self._stopped.set()
        try:
            if self._stream is not None and hasattr(self._stream, "stop"):
                self._stream.stop()
        except Exception:
            logger.exception("ka9q stream stop failed")
        self._stream = None

    # ---- ka9q callback ------------------------------------------------------

    def _on_samples(self, samples, quality) -> None:
        """Runs on the ka9q-python RX thread."""
        if self._stopped.is_set():
            return
        if self._anchor_first_rtp is None:
            first_rtp = getattr(quality, "first_rtp_timestamp", None)
            if first_rtp is not None:
                self._anchor_first_rtp = int(first_rtp)
        arr = np.asarray(samples, dtype=np.complex64)
        # Resequencer-garbage sanitisation (codar-sounder lessons): NaN
        # and overflow values would poison the autocorrelation downstream.
        if not np.all(np.isfinite(arr)):
            arr = np.where(np.isfinite(arr), arr, np.complex64(0))
        too_large = np.abs(arr) > 100.0
        if np.any(too_large):
            arr = np.where(too_large, np.complex64(0), arr)
        try:
            self._sample_queue.put_nowait(arr)
        except _q.Full:
            try:
                dropped = self._sample_queue.get_nowait()
                self._dropped_samples += int(getattr(dropped, "size", 0))
                self._sample_queue.put_nowait(arr)
                logger.warning(
                    "sample queue full at %d Hz; dropped %d samples "
                    "(cumulative %d) — frame labels advance over the gap",
                    self.frequency_hz, int(getattr(dropped, "size", 0)),
                    self._dropped_samples,
                )
            except Exception:
                pass

    # ---- RTP-reference timing -----------------------------------------------

    def _compute_anchor_utc(self) -> datetime:
        """UTC of the very first sample delivered, derived from the RTP
        counter (ka9q rtp_to_wallclock against the captured
        first_rtp_timestamp + channel_info) plus the hf-timestd authority
        offset.  This is the §4.5 RTP-reference invariant in concrete form:
        time is hf-timestd's product; the client consumes it and never
        re-samples the host clock per frame.  Falls back to wall-clock-now
        with an explicit warning only if the RTP timestamp was never
        captured or rtp_to_wallclock returns None."""
        import time as _time
        from ka9q.rtp_recorder import rtp_to_wallclock  # type: ignore

        reader = self._authority_reader
        if reader is None:
            from hf_tec.core.authority_reader import AuthorityReader
            reader = self._authority_reader = AuthorityReader()
        snap = None
        try:
            snap = reader.read()
        except Exception as exc:                # noqa: BLE001
            logger.warning("authority read failed: %s", exc)
        offset_sec = snap.offset_seconds if (snap and snap.offset_usable) else 0.0

        # time.time() is only a wrap-disambiguation hint for rtp_to_wallclock
        # (±period/2 tolerance, hours-scale) — NOT the labeling reference.
        # The actual label is the RTP-derived value plus the authority offset.
        utc_sec: Optional[float] = None
        if self._anchor_first_rtp is not None and self._channel_info is not None:
            utc_sec = rtp_to_wallclock(
                self._anchor_first_rtp,
                self._channel_info,
                wallclock_hint_sec=_time.time() + offset_sec,
            )
        if utc_sec is None:
            logger.warning(
                "HfTecSource: frame anchor falling back to wall-clock — "
                "RTP timing info unavailable (anchor_first_rtp=%r, "
                "channel_info=%r). Labels tied to host clock until "
                "hf-timestd authority + RTP become available.",
                self._anchor_first_rtp, self._channel_info,
            )
            return datetime.now(timezone.utc)
        anchor = datetime.fromtimestamp(utc_sec, tz=timezone.utc) + timedelta(
            seconds=offset_sec,
        )
        logger.info(
            "HfTecSource: frame anchor %s (rtp=%d, authority=%s, "
            "offset=%+.6fs) @ %d Hz",
            anchor.isoformat(), self._anchor_first_rtp,
            (snap.t_level_active if snap else "unavailable"),
            offset_sec, self.frequency_hz,
        )
        return anchor

    # ---- frame iterator -----------------------------------------------------

    def frames(self) -> Iterator[IqFrame]:
        """Yield one IqFrame per code period.

        Frames are accumulated from the streaming sample queue.  Each
        frame is exactly ``frame_n_samples`` complex64 samples.
        """
        if self._stream is None:
            self.open()
        assert self._sample_queue is not None  # noqa: S101

        buf = np.empty(self.frame_n_samples, dtype=np.complex64)
        filled = 0
        last_rx = _time.monotonic()

        while not self._stopped.is_set():
            try:
                chunk = self._sample_queue.get(timeout=1.0)
            except _q.Empty:
                if (
                    self.stall_timeout_s > 0
                    and _time.monotonic() - last_rx > self.stall_timeout_s
                ):
                    raise SourceStalled(
                        f"no I/Q for {self.stall_timeout_s:.0f}s at "
                        f"{self.frequency_hz} Hz (radiod {self.radiod_id!r} "
                        f"stalled); forcing re-subscribe"
                    )
                continue
            last_rx = _time.monotonic()
            idx = 0
            while idx < chunk.size:
                take = min(chunk.size - idx, self.frame_n_samples - filled)
                buf[filled : filled + take] = chunk[idx : idx + take]
                filled += take
                idx += take
                if filled == self.frame_n_samples:
                    # Frame ready.  Anchor the first sample's UTC once off the
                    # RTP counter (+ authority offset), then label every frame
                    # by pure sample-count projection — never re-sampling the
                    # host clock per frame (METROLOGY.md §4.5).
                    if self._anchor_utc is None:
                        self._anchor_utc = self._compute_anchor_utc()
                    # Real elapsed samples = contiguously-framed samples plus
                    # any dropped on overflow.  Dropped chunks are always the
                    # consumer's next-in-line, so folding the running drop count
                    # in keeps the label on real (RTP) time instead of letting a
                    # backlog slide every later frame earlier than it occurred.
                    dropped = self._dropped_samples
                    ts = self._anchor_utc + timedelta(
                        seconds=(self._frame_index * self.frame_n_samples + dropped)
                        / self.sample_rate_hz,
                    )
                    yield IqFrame(
                        frequency_hz=self.frequency_hz,
                        sample_rate_hz=self.sample_rate_hz,
                        samples=buf.copy(),
                        timestamp_utc=ts,
                        rtp_anchor_ns=self._anchor_first_rtp,
                        radiod_id=self.radiod_id,
                        dropped_samples_before=dropped,
                    )
                    self._frame_index += 1
                    filled = 0
