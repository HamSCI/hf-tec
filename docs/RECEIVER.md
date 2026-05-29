# hf-gps-tec receiver methodology

This document describes the high-frequency (HF) pseudorandom-noise-
(PRN-) coded beacon network that `hf-gps-tec` is built to receive,
the receiver digital-signal-processing (DSP) chain, the output
schema, and the waveform-spec status (formerly open gaps,
resolved by Hysell on 2026-05-29).

The reference design follows Hysell, Baumgarten, Milla, Valdez &
Kuyeng (2018, *J. Geophys. Res.: Space Physics* 123:6851–6864) §2,
with the network-topology and observables update from Aricoche &
Hysell (2024, *J. Geophys. Res.: Machine Learning & Computation*
1:e2024JH000270).

`README.md` carries the entry-door overview and install commands.
`docs/OVERVIEW.md` carries the project summary, transmit and
receive architecture, data products, and HamSCI / ionospheric-
science rationale.  This file is the deep technical reference for
the receive-side implementation.

## Contents

1. [The Hysell network](#1-the-hysell-network)
2. [Signal waveform](#2-signal-waveform)
3. [Receiver DSP chain](#3-receiver-dsp-chain)
4. [Observables and output schema](#4-observables-and-output-schema)
5. [Scientific value as opportunistic ionospheric input](#5-scientific-value-as-opportunistic-ionospheric-input)
6. [Waveform-spec status](#6-waveform-spec-status-formerly-open-gaps)
7. [References](#7-references)

---

## 1. The beacon network

Continuous-wave HF transmitters operated under Dr. David Hysell's
leadership at Cornell University.  The first generation of the
network operated in Peru and was documented in Hysell et al.
(2018) and Aricoche & Hysell (2024) — three transmit sites (Ancon,
Sicaya, Ica) feeding six JRO-administered receive sites (Jicamarca,
Huancayo, Mala, La Merced, Barranca, Oroya).  That deployment is
no longer the active transmit infrastructure; the receive-side DSP
chain described in this document is reused unchanged for the new
deployment.

**Current transmit sites** (North American deployment, per
direct correspondence with Dr. Hysell, 2026-05-29):

| Site                                  | Status               | Location              | PRN seed |
|---------------------------------------|----------------------|-----------------------|---------:|
| Poker Flat, Alaska                    | operational          | 65.1175°N, −147.4319°E | 0       |
| Gakona, Alaska                        | operational          | 62.3892°N, −145.1358°E | 1       |
| Palmer, Alaska                        | down for maintenance | 61.5656°N, −149.2517°E | 2       |
| Ithaca, New York (Cornell University) | planned              | ≈ 42.45°N, ≈ −76.47°E  | TBD     |

Palmer is down for maintenance as of 2026-05-29 with an on-site
repair visit planned for July 2026.  Cornell is planned but not
yet on-air; no PRN seed is assigned and the recorder will skip
it with a warning.  See `data/stations.toml` for full
coordinates and notes.

Each transmit site radiates **0.5 W continuous power per frequency**
into inverted-V antennas (per Hysell 2018 §2; antenna and power
figures are presumed to carry over from the Peru deployment —
Hysell's 2026-05-29 correspondence did not specifically restate
them for the Alaska sites).  Both transmit frequencies are
emitted simultaneously from each site.

**Receive sites.**  Not centrally administered in the current
deployment.  Any sigmond station with HF reception infrastructure
can run `hf-gps-tec` and contribute observations; the local
station identifies itself via the `[station]` block in the
recorder config.  The Peru deployment used dual-antenna receive
sites (northeast-southwest plus northwest-southeast) to permit
polarization and arrival-angle (interferometric) measurement; this
scaffolding processes a single antenna per site.

Timing at every transmit site is disciplined by the Global
Positioning System (GPS), anchoring the transmit code-epoch grid
to Coordinated Universal Time (UTC) at sub-microsecond accuracy.
For the pseudorange observable to be quantitatively meaningful at
the receiver, the receive-side sample clock should be similarly
GPS-disciplined — typically via a GPS-disciplined oscillator
(GPSDO) feeding the front-end's reference input, as the sibling
`gpsdo-monitor` sigmond client expects.  A free-running receive
clock would still permit Doppler measurement but the absolute
pseudorange would drift.

## 2. Signal waveform

Both frequencies — **2.9 MHz and 3.4 MHz** — carry a
**unique-per-transmitter continuous-phase PSK signal** modulated
by a pseudorandom noise (PRN) code (NOT BPSK — see below).

Per Hysell (direct correspondence, 2026-05-29) and Hysell 2018 §2:

| Parameter                  | Present-day value | Planned value | Derived |
|----------------------------|------------------|---------------|---------|
| Chip duration              | 10 µs            | 20 µs         | Null-to-null BW ≈ 100 kHz (present) / 50 kHz (planned) |
| Compression ratio          | 10 000           | 5 000         | Code length in chips |
| Code repetition period     | 100 ms           | 100 ms        | = chips × chip duration |
| Modulation                 | Continuous-phase PSK | (unchanged) | Per-chip complex symbol on unit circle, uniform phase |
| Code gain per Doppler bin  | 1 × 10⁶ (60 dB)  | 5 × 10⁵ (≈57 dB) | chips × 10² (coherent reps) |

**The PRN code is complex, not real BPSK.**  Hysell's reference
generator `create_pseudo_random_code(clen, seed)` returns a length-
`clen` array of unit-magnitude complex phases drawn uniformly from
[0, 2π).  The receiver replica must therefore be complex; treating
each chip as ±1 (BPSK) would mis-correlate.

**Per-transmitter seeds** (canonical mapping, do not renumber):

  - `seed=0` → Poker Flat
  - `seed=1` → Gakona
  - `seed=2` → Palmer
  - Cornell — seed TBD when it comes on-air

The receiver discriminates co-channel transmitters by correlating
against each one's distinct replica in parallel.  Per Hysell
(2026-05-29), the planned migration to 50 kHz BW reflects his
view that 100 kHz "probably exceeds the channel capacity much of
the time"; the cutover is a single configuration change in
`hf-gps-tec-config.toml`.

**Daytime D-region absorption.**  At 2.9/3.4 MHz the D-region
nearly kills the signal during daylight hours.  At high northern
latitudes in summer there is effectively no night, so receivers
should expect little to no detection until autumn shortens the
day.

## 3. Receiver DSP chain

The reference receiver in Hysell 2018 §2 samples directly at
10 mega-samples per second (MS/s) at an intermediate frequency
(IF) ≈3.18 MHz (the midpoint between the two carriers), then
digitally down-converts and decimates in two stages — first to
1 MS/s across the band, then per-carrier to 100 kilo-samples
per second (kS/s) I/Q at baseband for each frequency channel.
Two carriers in, two narrow baseband streams out.

`hf-gps-tec` lets `radiod` (ka9q-radio) own all the
down-conversion and decimation, producing the same per-carrier
baseband streams directly: one ka9q-radio channel per Tx
frequency, 100 kS/s in-phase / quadrature (I/Q) baseband
(Nyquist ±50 kHz, sufficient for the PRN's ≈100 kHz null-to-null
bandwidth).  This is mathematically equivalent to Hysell's
single-IF capture + per-carrier digital down-conversion — both
deliver each carrier's PRN signal to the DSP as 100 kS/s
baseband I/Q.  Pulling the per-carrier down-conversion into
`radiod` avoids re-implementing in Python what `radiod` already
does natively (and is also the pattern the peer recorders
`codar-sounder` and `hfdl-recorder` use for per-band channels).
The remaining DSP runs in Python.

The chain matches Hysell §2 stage-for-stage:

```
ka9q-radio channel @ 2.9 MHz (or 3.4 MHz), 100 kS/s complex I/Q
  │
  ▼
Frame to 100 ms blocks (10,000 samples) aligned to Coordinated
Universal Time (UTC) epoch grid
  │   Hysell (2026-05-29) confirmed code repetition on 100-ms UTC tics.
  ▼
PRN correlator bank (one replica per known Tx on this frequency)
  │   fast Fourier transform (FFT)-based circular cross-correlation:
  │     r_n[k] = IFFT( FFT(rx_frame) · conj(FFT(replica_n)) )
  │   → complex range profile, 10,000 bins × 1500 m/bin
  ▼
Coherent integrator (100 successive range profiles → 10 s)
  │   Stack into a 100 × 10,000 complex matrix
  │   FFT along axis 0 (slow-time) → range-Doppler matrix
  │   Doppler resolution = 1 / 10 s = 0.1 Hz
  │   Doppler ambiguity = 1 / 100 ms = ±5 Hz
  │   Post-coherent gain ≈ 60 dB total (40 dB code + 20 dB Doppler)
  ▼
Incoherent integrator (6 × 10 s power averaging → 1 min)
  │   |range-Doppler|² averaged over 6 coherent windows
  ▼
First-hop detector
  │   Scan range bins outward from short range; find first bin
  │   exceeding (noise_floor + snr_threshold_db) where snr is the
  │   signal-to-noise ratio (SNR).
  │   → pseudorange (km) = bin_index × 1.5
  │   In that range bin, compute Doppler first moment
  │   → Doppler shift (Hz)
  │   Peak power in that bin → amplitude (dB above noise floor)
  ▼
Per-minute record emitted to JSON Lines (JSONL) + HamSCI sink
```

### 3.1 Channel configuration via ka9q-python

Each frequency is opened as a separate `MultiStream` subscription.
The recorder explicitly overrides the `iq`-preset audio filter via
`ensure_channel(low_edge_hz=-50000, high_edge_hz=+50000)` so the full
PRN bandwidth survives — equivalent to the wideband-filter wiring
codar-sounder uses for the CODAR chirp band.

### 3.2 Doppler ambiguity vs ionospheric reality

At 3.4 MHz, ±5 Hz of Doppler ambiguity corresponds to ±220 m/s
line-of-sight velocity, comfortably above any expected ionospheric
Doppler in the equatorial F region (which Hysell 2024 reports as
peaking near 30 m/s during the prereversal enhancement).  The 0.1 Hz
Doppler bin is ≈4 mm/s, finer than needed but cheap.

### 3.3 Code-free detection mode

The DSP chain above describes the **locked mode** that requires
the per-transmitter PRN code.  Until that specification is
supplied by the network operator (§6 Gap 1), the daemon runs in
**code-free mode** instead, implemented in
`core/detect_codeless.py` and `core/codeless_pipeline.py`.

The discriminating property the detector exploits is that every
PRN-coded beacon of this family repeats its waveform exactly
every 100 ms by construction.  The normalised lagged
autocorrelation at lag τ = one code period is therefore:

```
r(τ) = ⟨ s(t) · s*(t + τ) ⟩ / ⟨ |s(t)|² ⟩
```

For Gaussian noise alone this averages to ≈ 0 (with variance
∝ 1/N).  For a periodic-at-τ signal with power P_s in noise
power P_n,

```
|r(τ)| ≈ P_s / (P_s + P_n)
arg r(τ) = −2π · f_d · τ          → Doppler shift f_d
```

A reference autocorrelation at a non-code-period lag (default
137 ms, chosen to be coprime with the code period) gives the
noise floor.  The detection statistic is the dB ratio of the
two magnitudes; default threshold 6 dB.

```
60-s buffer of contiguous I/Q at 100 kS/s   (≈ 6 million samples)
  │
  ▼
lagged_autocorr(buf, lag = 10,000 samples)  ← r(τ = code period)
lagged_autocorr(buf, lag = 13,700 samples)  ← r(τ = reference)
  │
  ▼
autocorr_db = 20·log10(|r_code| / |r_ref|)
doppler_hz  = −arg(r_code) / (2π · 0.1)
snr_estimate_db = 10·log10(|r_code| / (1 − |r_code|))
  │
  ▼
One JSONL record per integration window emitted to
  /var/lib/hf-gps-tec/<radiod>/codeless/YYYY/MM/DD.jsonl
plus an additive row in sigmond's hf_gps_tec_codeless.spots
```

**What this mode gives:**

- Confirmation that a PRN-coded beacon is present on the channel
  — the autocorrelation magnitude is far above the floor when
  any such beacon is reaching the receiver.
- Doppler shift, unambiguous within ±5 Hz (= ±200 m/s
  line-of-sight at 3.4 MHz).
- Rough received-SNR estimate.
- Band-power time series — directly useful as a propagation
  monitor.

**What this mode cannot give:**

- Per-transmitter identification.  All PRN codes share the
  100 ms period, so co-band transmitters sum into a single
  detection statistic.
- Pseudorange (group delay).  That requires a code-phase
  reference, which the autocorrelation does not provide.

**Sensitivity.**  For input SNR = −20 dB (signal is 1 % of total
received power), |r_code| ≈ 0.01 and the noise floor at
N = 6 × 10⁶ samples is ≈ 4 × 10⁻⁴ — about 28 dB of headroom over
threshold.  Marginal-propagation paths (input SNR closer to
−30 dB) yield ~8 dB headroom; longer integration windows
(parameter `codeless_integration_seconds`) buy additional
detection margin at the cost of cadence.

**Mode selection.**  The daemon picks between locked and
code-free modes from `[mode] mode` in the config:

- `auto` — code-free when `correlate.PRN_IS_STUB` is `True`,
  locked otherwise.  This is the default and the recommended
  setting.  Since Hysell's real generator landed on 2026-05-29,
  `auto` now resolves to `locked` by default; the codeless
  fallback remains in place for any future stub regression.
- `codeless` — always code-free.  Useful for first-light beacon-
  presence verification before committing to locked-mode
  pseudorange records.
- `locked` — always locked.  Requires that every enabled Tx has
  a `prn_seed` assigned in `data/stations.toml`.

`inventory --json` reports both the configured and resolved mode
per instance under `mode_configured` / `mode_resolved`.

## 4. Observables and output schema

One JSONL record per (transmitter, receiver, frequency) per minute,
written to:

```
/var/lib/hf-gps-tec/<radiod_id>/YYYY/MM/DD.jsonl
```

and, when sigmond's local sink is writable, mirrored as one row in
the `hf_gps_tec.spots` table of `/var/lib/sigmond/sink.db`.

### Fields

| Field            | Meaning |
|------------------|---|
| `time`           | UTC timestamp at the end of the 1-min incoherent window. |
| `tx_id`          | Transmitter site identifier (`POKER_FLAT`, `GAKONA`, `PALMER`, …). |
| `rx_id`          | This receiver's station identifier. |
| `radiod_id`      | The radiod this recorder bound to. |
| `frequency_hz`   | Centre frequency (2.9e6 or 3.4e6). |
| `pseudorange_km` | Group delay × c / 2, first-hop X-mode (Hysell 2018 §2). |
| `doppler_hz`     | First moment of Doppler spectrum in the first-hop range bin. |
| `amplitude_db`   | Peak power in the first-hop bin, dB above incoherent noise floor. |
| `snr_db`         | First-hop peak SNR. |
| `n_hops`         | 1 (first-hop only at v0.1.0; multi-hop is future work). |
| `lock_quality`   | 0–1 heuristic (peak prominence + slow-time phase consistency). |
| `noise_floor_db` | Estimated incoherent noise floor in the range-Doppler matrix. |
| `processing_version` | `hf-gps-tec` version string. |
| `contract_version`   | `0.8`. |

The schema is upstream-compatible with the `.out.mod` text format
that Hysell's inversion code (`focus.c`) consumes — emitting that
flavour from the same per-minute record is a small additional sink
(deferred to a follow-up).

## 5. Scientific value as opportunistic ionospheric input

The Hysell network meets the criteria for a useful opportunistic HF
propagation source for HamSCI:

1. **Known transmit geometry.**  Each transmit site's location is
   public to sub-degree accuracy and stable.
2. **Known frequencies and codes.**  Two stable carriers per site,
   each with a fixed PRN code.
3. **GPS-disciplined timing.**  Every transmission carries an
   absolute UTC reference at sub-microsecond accuracy.
4. **Continuous operation.**  0.5 W of continuous wave per
   frequency, 24/7.
5. **Rich per-frame telemetry.**  Pseudorange, Doppler, amplitude,
   and (with dual-antenna sites) polarization and arrival angle —
   more per-spot information than WSPR or FT8 carries.
6. **Both endpoints known when a Tx is decoded** — each detection
   nails down a complete great-circle propagation path with known
   geometry on both ends.
7. **Two-frequency diversity per Tx.**  Pseudorange and Doppler are
   different moments of the ionospheric electron-density profile;
   two frequencies give two independent constraints.

The recorder's outputs are sized to feed Hysell's existing regional
inversion (Aricoche & Hysell 2024) directly — group delay, Doppler,
amplitude at 1-min cadence is exactly the input format that
`focus.c` ingests.

## 6. Waveform-spec status (formerly open gaps)

These gaps were blocking locked-mode operation; all three
critical items are now resolved (Hysell, 2026-05-29).

### Gap 1 — PRN code specification — RESOLVED

Hysell supplied the per-station generator routine
`create_pseudo_random_code(clen, seed)`:

```python
def create_pseudo_random_code(clen=10000, seed=0):
    numpy.random.seed(seed)
    phases = numpy.array(
        numpy.exp(1.0j * 2.0 * math.pi * numpy.random.random(clen)),
        dtype=numpy.complex64,
    )
    return phases
```

Notes that bit the implementation in `core/correlate.py`:

- The code is **complex random-phase**, not real BPSK.  Each
  chip is on the unit circle with uniform phase in [0, 2π).
- Reproducing the exact sequence requires legacy numpy
  `RandomState` (Mersenne Twister); `np.random.default_rng`
  (PCG64) yields different numbers for the same seed and would
  silently desync from the transmitter.  `test_generator_matches_hysell_reference`
  guards against accidental regression here.
- Per-station seed: 0 = Poker Flat, 1 = Gakona, 2 = Palmer.
  Cornell seed TBD when it comes on-air.
- Same seed is used at both 2.9 MHz and 3.4 MHz for a given
  station — frequencies do not key the code.

### Gap 2 — UTC code-epoch alignment — RESOLVED

Hysell confirmed (2026-05-29) that the codes repeat on
100-ms-aligned UTC tics — the natural choice for a
GPS-disciplined network.  Code-epoch grid: `t_chip0 mod 100 ms = 0`.

### Gap 3 — Amplitude calibration reference (lower priority)

Still open.  Hysell 2024 reports amplitude (in dB) without
specifying whether it is calibrated to an absolute reference,
relative to in-band noise, or referenced to a synthetic peak.
Since `hf-gps-tec` reports dB above its own noise floor, this
only matters when comparing across receivers; the inversion
uses each receiver's series internally, so this gap is not
blocking.

### What's *not* a gap

- The receiver DSP chain (Hysell 2018 §2 is fully specified).
- The output schema (specified by what `focus.c` reads).
- The network topology (current sites listed in §1; new sites
  can be added by editing `data/stations.toml`).
- The radiod channel configuration (matches the codar-sounder
  wideband-IQ pattern).

Adding a new transmitter to the network is now a config-only
change: add a `[transmitters.<SITE>]` block with `prn_seed = N`
to `data/stations.toml`, add `<SITE>` to `[transmitters].enabled`
in the recorder config, and reload.  No code change needed.

## 7. References

- Hysell, D. L., Baumgarten, Y., Milla, M. A., Valdez, A., & Kuyeng, K.
  (2018).  "Ionospheric Specification and Space Weather Forecasting
  with an HF Beacon Network in the Peruvian Sector."  *J. Geophys.
  Res.: Space Physics* 123, 6851–6864.
  [doi:10.1029/2018JA025648](https://doi.org/10.1029/2018JA025648).
- Aricoche, J. A. & Hysell, D. L. (2024).  "Ionospheric Radio Beacon
  Signal Analysis and Parameter Estimation Using Automatic
  Differentiation."  *J. Geophys. Res.: Machine Learning &
  Computation* 1, e2024JH000270.
  [doi:10.1029/2024JH000270](https://doi.org/10.1029/2024JH000270).
- Hysell, D. L., Milla, M. A., & Vierinen, J. (2016).  "A multistatic
  HF beacon network for ionospheric specification in the Peruvian
  sector."  *Radio Science* 51, 392–401.
  doi:10.1002/2016RS005951.  Earlier network reference.
