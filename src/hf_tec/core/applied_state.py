"""The §3 ``timing_authority_applied`` report for one hf-tec instance.

CLIENT-CONTRACT §18.5 (amendment 2026-09-04): the field describes the
LABELS a client writes, not its reading habits.  One hf-tec instance runs
one pipeline worker per frequency.  Each worker's source pins one
:class:`hamsci_dsp.timing.AnchorUTC` and projects every frame label off it.
The instance's labels ride the authority only when every anchored source's
do.  A mixed state stays legal (§18.7) but must stay visible, so it reports
null here, never an average.  The channel counts travel with the block so
the reader can see how many labels the report stands for.

The daemon writes the result once a minute to
``<data_root>/<instance>/timing-authority.json`` through
``hamsci_dsp.timing.write_applied_state``; ``hf-tec inventory --json``
(a separate process) reads it back.  Both sides take the path from
:func:`applied_state_path` and the instance from :func:`instance_name`, so
they can never name different directories.

``DEFAULT_DATA_ROOT`` lives here rather than in ``output.py`` because
``output.py`` imports ``contract`` and ``contract`` needs this module.
``output.py`` re-exports the same object.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from hamsci_dsp.timing import AnchorUTC, applied_state_for_anchors

from ..config import Config


DEFAULT_DATA_ROOT = Path("/var/lib/hf-tec")

APPLIED_STATE_FILENAME = "timing-authority.json"


def applied_state_path(data_root: Path, instance: str) -> Path:
    """Where the daemon for ``instance`` leaves its applied block.  The
    same directory OutputSink writes the JSONL spool under."""
    return Path(data_root) / instance / APPLIED_STATE_FILENAME


def instance_name(cfg: Config, explicit: Optional[str] = None) -> Optional[str]:
    """The instance directory name, resolved the way the daemon resolves it.

    systemd passes ``--instance %i``; a manual ``hf-tec daemon`` or
    ``hf-tec inventory`` may not.  Fall back to ``[instance].reporter_id``,
    then to ``[ka9q].status_address``.  Returns None when nothing names the
    instance; the daemon refuses to start in that case, and inventory
    reports under ``"default"``.
    """
    return explicit or cfg.instance.reporter_id or cfg.ka9q.status_address or None


def _anchor_of(worker) -> Optional[AnchorUTC]:
    """The anchor a worker's running pipeline pinned, or None when the
    worker has no pipeline (crashed, waiting out its backoff) or its source
    has not framed yet."""
    pipeline = getattr(worker, "pipeline", None)
    if pipeline is None:
        return None
    return getattr(getattr(pipeline, "source", None), "anchor", None)


def applied_state_for(
    workers: Iterable,
    client_radiod: str,
    now_fn: Optional[Callable[[], float]] = None,
) -> Optional[dict]:
    """Aggregate the workers' anchors into one §3 block, or None.  The
    suite-shared rule in ``hamsci_dsp.timing.applied_state_for_anchors``
    decides: populated iff every anchored source carries the correction,
    with counts attached."""
    return applied_state_for_anchors(
        (_anchor_of(w) for w in workers),
        client_radiod=client_radiod, now_fn=now_fn,
    )
