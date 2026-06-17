"""sigmond Receiver Channels TUI parser for hf-tec.

Loaded by sigmond at TUI time via ``[client_features.receiver_channels]``
in ``deploy.toml``.  hf-tec forces F32LE IQ
(hf_tec.core.stream calls ensure_channel(encoding=4) to dodge an
S16BE byte-swap pathology in ka9q-python); the parser mirrors that
choice unconditionally.
"""

from __future__ import annotations

from typing import Optional

from sigmond.ka9q_encoding import ENCODING_INTS


def parse_receiver_channels(
    cfg: dict,
) -> tuple[str, set[int], Optional[int]]:
    """Return ``(status_dns, configured_freqs_hz, encoding_int)`` from
    an hf-tec per-instance config.

    Status address is under [ka9q].status_address (newer naming;
    hf-timestd uses the older [ka9q].status form).  Frequencies are
    every [[frequency]] block where ``enabled = true``; the
    ``sample_rate_hz`` field is informational, not a receiver-channel
    filter.
    """
    ka9q = cfg.get("ka9q") or {}
    status = str(ka9q.get("status_address") or "")
    freqs: set[int] = set()
    for entry in cfg.get("frequency", []) or []:
        if not entry.get("enabled", True):
            continue
        hz = entry.get("center_hz")
        if hz is None:
            continue
        try:
            freqs.add(int(hz))
        except (TypeError, ValueError):
            continue
    return status, freqs, ENCODING_INTS["f32"]
