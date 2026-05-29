# hf-gps-tec

A sigmond software-defined-radio (SDR) client that receives
pseudorandom-noise- (PRN-) coded high-frequency (HF)
ionospheric beacons and produces line-integrated electron-density
observables — equivalent to total electron content (TEC) derived
from the Global Positioning System (GPS-TEC) — sampled along
oblique HF propagation paths rather than along near-zenith
satellite paths.

The instrument and signal design follow the HF beacon network
documented by Hysell et al. (2018) and Aricoche & Hysell (2024).
That network's first generation operated in Peru; the current
operational transmit network is being established in North
America under Dr. David Hysell's leadership at Cornell University,
with three transmitters in Alaska (Poker Flat and Gakona on-air;
Palmer down for a July 2026 maintenance visit) and a fourth
planned at Ithaca, New York.  See [`docs/OVERVIEW.md`](docs/OVERVIEW.md) §3
for details.

## Status

**v0.1.0 — scaffolding.**  Full digital signal processing (DSP)
pipeline implemented and tested with synthetic signals.  Two
operating modes are available:

- **codeless** — 100-ms-autocorrelation detector confirms beacon
  presence and recovers Doppler shift without using the PRN
  code.  Suitable for first-light "is the signal getting here?"
  tests.  Records emitted to
  `/var/lib/hf-gps-tec/<radiod>/codeless/`.
- **locked** (default) — full PRN correlator (per-transmitter
  pseudorange + Doppler + amplitude).  Records go to
  `/var/lib/hf-gps-tec/<radiod>/locked/`.

Mode selection is controlled by the `[mode]` block in the config
(`auto`, `codeless`, or `locked`); the default `auto` setting
resolves to `locked` now that Hysell's per-station PRN generator
is wired in (2026-05-29).

## Install

```bash
# First-run install — creates the service user, builds the venv,
# renders config templates, installs the systemd unit.  Idempotent.
sudo ./scripts/install.sh

# Edit /etc/hf-gps-tec/hf-gps-tec-config.toml to set station
# identity and the ka9q-radio status hostname.

# Validate the config (JSON output).
sudo -u hfgpstec hf-gps-tec validate --json

# Start one instance per ka9q-radio radiod on the host.
sudo systemctl start hf-gps-tec@<radiod-id>

# Watch detections accumulate (codeless mode, default).
tail -f /var/lib/hf-gps-tec/<radiod-id>/codeless/*.jsonl

# After the PRN spec lands and locked mode kicks in:
tail -f /var/lib/hf-gps-tec/<radiod-id>/locked/*.jsonl
```

## Further reading

- [`docs/OVERVIEW.md`](docs/OVERVIEW.md) — project summary,
  transmit and receive architecture, data products, and the
  scientific case (HamSCI objectives, contributions to
  ionospheric study).
- [`docs/RECEIVER.md`](docs/RECEIVER.md) — receive-side DSP
  methodology with numeric defaults, references back to the
  Hysell papers, and the current spec-gap list.
- [`CLAUDE.md`](CLAUDE.md) — developer and operator briefing.
- [`config/hf-gps-tec-config.toml.template`](config/hf-gps-tec-config.toml.template)
  — full configuration schema with inline comments.

## License

MIT — see [`LICENSE`](LICENSE).
