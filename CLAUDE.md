# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when
working with code in this repository.

## What this project is

**hf-gps-tec** is a Python client for the HamSCI (Ham Radio
Science Citizen Investigation) sigmond suite that mimics the receive
sites of the high-frequency (HF) PRN-coded beacon network described
by Hysell et al. (2018, *JGR Space Physics* 123:6851–6864) and
Aricoche & Hysell (2024, *JGR ML&C* 1).  It subscribes to per-frequency
I/Q multicast streams from `radiod` via `ka9q-python`, correlates each
frame against a bank of pseudorandom-noise replicas, and produces
JSON-Lines (JSONL) per-minute records of pseudorange, Doppler shift,
and amplitude for each (transmitter, receiver, frequency) link.

Part of the HamSCI sigmond suite — see
`/opt/git/sigmond/sigmond/CLAUDE.md` (orchestrator) and
`/opt/git/sigmond/CLAUDE.md` (umbrella) for cross-repo context.
Follows the same Pattern A install layout and contract surface as
`psk-recorder`, `wspr-recorder`, `hfdl-recorder`, `codar-sounder`,
and `hf-timestd`.

Documentation layout:

- `README.md` — top-level summary + status + install.
- `docs/RECEIVER.md` — receiver methodology (network description,
  waveform parameters, DSP chain, output schema, open gaps).
- this file — developer / operator briefing.

## Status

**v0.1.0 — scaffolding, locked-mode-capable as of 2026-05-29.**
Full DSP pipeline structured.  Hysell's per-station PRN generator
is wired in (`core/correlate.py:generate_prn_code`); the recorder
defaults to locked mode.  Per-Tx seeds in `data/stations.toml`:
Poker Flat = 0, Gakona = 1, Palmer = 2.  Cornell is still planned
and has no seed assigned, so the pipeline skips it with a warning.

Outstanding from `docs/RECEIVER.md` §6: amplitude calibration
reference (lower priority — only affects cross-receiver
comparison, not the per-receiver inversion).  All other
previously-open gaps are closed.

## Authors

- Michael Hauan (AC0G, GitHub: mijahauan)
- Repo: https://github.com/mijahauan/hf-gps-tec (pending)

## Quick reference

```bash
# Development — uv canonical
uv sync --extra dev
uv run pytest tests/ -v
uv run pytest tests/test_correlate.py -v       # one file
uv run pytest -k contract -v                   # by keyword

# Production install / upgrade (uses sigmond's shared _ensure_uv helper)
sudo ./scripts/install.sh           # first-run: user, venv, config, systemd
sudo ./scripts/deploy.sh            # ongoing: refresh editable install + restart

# CLI (current — verify against `hf-gps-tec --help`)
hf-gps-tec inventory --json      # per-instance resource view
hf-gps-tec validate --json       # config validation
hf-gps-tec version --json        # version + git sha
hf-gps-tec status                # health check
hf-gps-tec daemon --config /etc/hf-gps-tec/hf-gps-tec-config.toml --radiod-id my-rx888
```

## Architecture

```
radiod (ka9q-radio, IQ preset)
  │  one channel per known Tx frequency (2.9 + 3.4 MHz)
  │  ka9q-python ensure_channel(low_edge, high_edge) → ~100 kHz BW,
  │  100 kS/s decimated I/Q
  ▼
hf-gps-tec daemon (one per radiod, = one systemd instance)
  │
  ├─ FreqPipeline(2.9 MHz)
  │    ├─ ka9q.MultiStream subscription → 100 ms frames (10,000 samples)
  │    ├─ ReplicaBank: one PRN replica per known Tx, precomputed FFT
  │    ├─ correlate frame against every replica in parallel (FFT-based)
  │    ├─ stack 100 successive range profiles (10 s coherent integration)
  │    ├─ slow-time FFT → range-Doppler matrix per Tx
  │    ├─ 6 × 10-s power averaging (1 min incoherent integration)
  │    └─ first-hop detector → (pseudorange, Doppler, amplitude) per Tx
  ├─ FreqPipeline(3.4 MHz)
  ▼
Output (core/output.py)
  │   one JSONL record per (Tx, Rx, freq) per minute
  │   /var/lib/hf-gps-tec/<radiod_id>/YYYY/MM/DD.jsonl
  │   additive hf_gps_tec.spots row via sigmond.hamsci_sink.Writer
```

## Project structure

```
src/hf_gps_tec/
  cli.py              # argparse entry; subcommands listed above
  config.py           # TOML loader, per-instance resolution
  contract.py         # inventory/validate JSON builders (contract v0.7)
  stations.py         # known Tx/Rx site database (loaded from
                      # /etc/hf-gps-tec/stations.toml)
  version.py          # GIT_INFO dict for provenance
  core/
    daemon.py         # HfGpsTecRecorder: orchestrates per-frequency pipelines
    stream.py         # HfGpsTecSource: ka9q-python wideband I/Q + framing
    correlate.py      # PRN code generator (STUB) + FFT-based correlator
    coherent.py       # coherent integration → range-Doppler matrix
    detect.py         # first-hop detector + Doppler 1st-moment + amplitude
    pipeline.py       # FreqPipeline: per-frequency orchestrator
    output.py         # JSONL writer + hamsci_sink writer
tests/                # config / contract / correlator / detector tests
config/
  hf-gps-tec-config.toml.template
data/
  stations.toml       # network topology — three Alaska Tx (Poker Flat,
                      # Gakona, Palmer) + planned Cornell; per-site
                      # prn_seed required for locked-mode correlation
systemd/
  hf-gps-tec@.service  # Template unit; %i = radiod_id
scripts/
  install.sh          # First-run bootstrap (Pattern A)
  deploy.sh           # Editable-install refresh
deploy.toml           # Sigmond deploy manifest
docs/
  RECEIVER.md         # Methodology and open gaps
```

## Key design decisions

- **One systemd instance per radiod** (`hf-gps-tec@<radiod_id>.service`),
  matching the other recorders.
- **One ka9q-radio channel per Tx frequency.** Wideband I/Q at ≈100 kS/s,
  with `low_edge`/`high_edge` overrides on `ensure_channel` so the
  full ±50 kHz around each Tx frequency is captured (matches Hysell §2's
  100 kS/s decimated rate).
- **FFT-based circular cross-correlation** for the PRN correlator —
  10,000-chip code, 10,000-sample frame, single 10,000-point FFT pair
  per (Tx, freq) per 100 ms.  Replica FFTs precomputed once at startup.
- **Coherent integration over 10 s** (100 successive code reps) yields
  0.1 Hz Doppler resolution and 100× post-correlator gain.
- **Incoherent integration over 1 min** (6 × 10 s power averaging)
  matches Hysell §2's 1-minute cadence.
- **First-hop only** (matches Hysell 2018 + 2024) — first range bin
  exceeding the configured SNR threshold above noise floor.
- **JSONL canonical L1 artefact**, additive hamsci sink for cross-client
  aggregation; optional `.out.mod` legacy writer for direct JRO
  inversion pipeline consumption is a follow-up.
- **PRN spec is the only blocker.** Once the JRO team supplies the
  per-Tx generator polynomial + seed, the correlator can be made
  real with no other architectural change.

## Client contract (v0.7)

`src/hf_gps_tec/contract.py` declares
`CONTRACT_VERSION = "0.7"`.  Authoritative spec:
`/opt/git/sigmond/sigmond/docs/CLIENT-CONTRACT.md`.

Sections implemented in scaffolding:

- **§1 / §2 / §3 / §4 / §5** — native TOML config, radiod-id binding,
  self-describe CLI (`inventory`/`validate`/`version` `--json`),
  templated systemd unit, `deploy.toml` manifest.
- **§6 / §7** — uses ka9q-python `MultiStream`; data destination read
  from `ChannelInfo`, never client-specified.
- **§8** — `RADIOD_<id>_CHAIN_DELAY_NS` read from `coordination.env`.
- **§10 / §11** — `log_paths` in inventory output; daemon process log
  goes to systemd journal.  `HF_GPS_TEC_LOG_LEVEL` /
  `CLIENT_LOG_LEVEL` honored on startup.
- **§12** — validate hardening (config presence, station list sanity,
  PRN-stub warning surfaced explicitly).
- **§17** — hamsci sink writer (`hf_gps_tec.spots`) alongside canonical
  JSONL.

Deferred:

- **§14** — config init/edit wizard via `sigmond.wizard_dispatch`
  (operator hand-edits the TOML for now).
- **§18 (timing authority)** — `core/authority_reader.py` subscribes to
  hf-timestd's `/run/hf-timestd/authority.json`; the frame anchor is
  derived from the RTP counter (`rtp_to_wallclock` + the published
  offset) and every frame label projects off it by sample count
  (`core/stream.py:_compute_anchor_utc`, METROLOGY §4.5 RTP-reference
  invariant) — matching codar-sounder.  The inventory
  `timing_authority_applied` field stays `null` (capability / RTP-default,
  same as the sibling clients).  Absolute code-epoch alignment for PRN
  sync is the remaining work on top of this anchor.

## Production paths

- Config: `/etc/hf-gps-tec/hf-gps-tec-config.toml`
- Stations DB: `/etc/hf-gps-tec/stations.toml`
- JSONL spool: `/var/lib/hf-gps-tec/<radiod_id>/YYYY/MM/DD.jsonl`
- Per-band logs: systemd journal — `journalctl -u hf-gps-tec@<radiod_id>`
- Venv: `/opt/hf-gps-tec/venv`
- Source: `/opt/git/sigmond/hf-gps-tec` (editable install)
- Service user: `hfgpstec:hfgpstec`

## References

- Hysell et al. (2018) — receiver architecture and DSP chain
  (§2 specifies the full chain: 10 MS/s ADC → 1 MS/s → 100 kS/s →
  100 ms code rep → 10 s coherent → 1 min incoherent).
- Aricoche & Hysell (2024) — current network topology (5 Tx, 6 Rx),
  expanded observables (amplitude added to the 2018 pseudorange +
  Doppler).
- `README.md` — operator-facing overview.
- `docs/RECEIVER.md` — methodology with explicit gap markers.
