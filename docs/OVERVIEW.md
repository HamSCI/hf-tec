# hf-tec — project overview

This document is the project-level summary.  It describes what
`hf-tec` is and is for, the transmit and receive architecture
of the high-frequency (HF) beacon network it is built around, the
data products it generates, and the scientific value of those data
products to the Ham Radio Science Citizen Investigation (HamSCI)
community and to ionospheric research more broadly.

[`README.md`](../README.md) carries the one-paragraph entry-door
description plus install instructions.
[`docs/RECEIVER.md`](RECEIVER.md) carries the deeper digital
signal processing (DSP) reference for the receive-side
implementation.

## Contents

1. [What `hf-tec` is](#1-what-hf-tec-is)
2. [Where the name comes from](#2-where-the-name-comes-from)
3. [Transmit architecture](#3-transmit-architecture)
4. [Receive architecture](#4-receive-architecture)
5. [Data products](#5-data-products)
6. [Service to HamSCI objectives](#6-service-to-hamsci-objectives)
7. [Contribution to ionospheric study](#7-contribution-to-ionospheric-study)
8. [Open work](#8-open-work)
9. [References](#9-references)

---

## 1. What `hf-tec` is

`hf-tec` is a software client in the sigmond software-defined
radio (SDR) suite.  It receives pseudorandom-noise- (PRN-) coded
HF ionospheric beacons that radiate from a small network of
fixed-location transmitters, correlates each received frame
against a pre-computed replica of every known transmitter's
code, extracts pseudorange (group delay), Doppler shift, and
amplitude per resolved propagation path, and writes a continuous
stream of one-record-per-minute observations to local storage
and to the HamSCI sink.

The instrument design and DSP chain follow Hysell, Baumgarten,
Milla, Valdez & Kuyeng (2018, *Journal of Geophysical Research:
Space Physics* 123:6851–6864) and the receiver / inversion update
in Aricoche & Hysell (2024, *Journal of Geophysical Research:
Machine Learning & Computation* 1:e2024JH000270).  Those papers
document the original deployment of the HF-beacon system in Peru;
the transmit network is now being re-established in North America
under Dr. David Hysell's leadership at Cornell University (see §3
below).  The methodology is the same; only the geography differs.

`hf-tec` is not the transmitter side of that system — there
is no transmit hardware in this project.  It is purely a
receive-and-decode client that can be deployed at any sigmond
station with appropriate antennas and a `ka9q-radio` `radiod`
instance configured to deliver the relevant HF channels.

## 2. Where the name comes from

The name has two parts: **HF**, the band the beacons occupy, and
**TEC**, the ionospheric observable the client ultimately produces.
The second is worth a word — `hf-tec` decodes a PRN-coded PSK signal,
so why call it a *TEC* instrument?  (It was formerly named
`hf-gps-tec`; the "gps" was dropped because it misled — the client does
not receive or depend on GPS.  It is named for what it *produces*, by
analogy with the established satellite technique below.)

The most common way to measure ionospheric TEC is **GPS-TEC**: reading
the differential delay and phase of Global Navigation Satellite System
(GNSS) signals as they transit the ionosphere line-of-sight from
satellite to ground.  Hysell et al. (2018) §2 shows that an HF skywave
beacon yields the *same* observables along its oblique path.  For
either signal class — line-of-sight GNSS transit through the
ionosphere, or oblique HF skywave reflection from it — the fundamental
observables are the same:

- the pseudorange, which is the propagation time of the signal
  multiplied by the speed of light in vacuum and reflects the
  group-path length through the medium; and
- the optical path length, recoverable by integrating the
  negative of the Doppler shift in time, which reflects the
  phase-path length.

Both observables deviate from the geometric raypath length by
amounts proportional to the line-integrated electron density
along the path — that is, the total electron content (TEC) of
the ionosphere along that raypath.  The HF case is more subtle
because the index of refraction has stronger frequency
dependence and depends on the geomagnetic field, electron
gyrofrequency, and electron-neutral collision frequency, but in
the small-deviation limit the relationship to TEC is the same.

`hf-tec` is, therefore, a GPS-TEC-style instrument that samples
TEC along oblique HF propagation paths rather than along
near-zenith GNSS paths.  The name reflects the ionospheric
observable the client ultimately produces (TEC) and the band it
produces it from (HF) — not the modulation it decodes
(continuous-phase PSK with a complex random-phase PRN code; see
§3.2) or the wave-mode it operates in (HF skywave).

## 3. Transmit architecture

The receiver is built to recover signals from the HF beacon
network whose waveform and DSP-chain reference design are
documented in Hysell et al. (2018, §2) and Aricoche & Hysell
(2024, §3).  The original deployment of that network was in
Peru; the current operational deployment is in North America
under Dr. David Hysell's leadership at Cornell University.  The
architecture is fixed enough that the client treats it as the
reference design, with a configurable list of transmit sites so
that future expansions of the network can be added by editing
one file.

### 3.1 Transmit sites

Three Alaska transmitters, with a fourth (Cornell) planned
(Hysell, 2026-05-29):

| Site                                  | Status                 | Latitude (°N) | Longitude (°E) | PRN seed |
|---------------------------------------|------------------------|--------------:|---------------:|---------:|
| Poker Flat, Alaska                    | operational            |     65.1175   |     −147.4319  | 0        |
| Gakona, Alaska                        | operational            |     62.3892   |     −145.1358  | 1        |
| Palmer, Alaska                        | down for maintenance   |     61.5656   |     −149.2517  | 2        |
| Ithaca, New York (Cornell University) | planned                |   ≈ 42.45     |    ≈ −76.47    | TBD      |

Palmer is offline as of 2026-05-29 pending an on-site repair
visit planned for July 2026; correlating against it in the
meantime is harmless (the receiver simply produces no peak for
that Tx).  Cornell is planned as a second-region transmitter but
has no PRN seed assigned yet, so the recorder logs a warning and
skips it until Hysell publishes that seed.

The per-site PRN seed selects the Hysell pseudorandom phase code
(see §3.2); the mapping is canonical — Poker = 0, Gakona = 1,
Palmer = 2 — and must not be renumbered.

Historical context: the network's first generation operated at
three sites in coastal and sub-Andean Peru (Ancon, Sicaya, Ica;
Aricoche & Hysell 2024, Table 1).  Those transmitters are no
longer the active reference for the receiver; the modulation
parameters and DSP chain are reused unchanged.

### 3.2 Radio-frequency parameters

Per Hysell (direct correspondence, 2026-05-29):

- **Carrier frequencies:** 2.9 megahertz (MHz) and 3.4 MHz,
  both emitted simultaneously from each transmit site.
- **Modulation:** continuous-phase PSK.  Each chip carries a
  unit-magnitude complex symbol whose phase is drawn uniformly
  from [0, 2π) — not BPSK.  The exact sequence is generated by
  Hysell's `create_pseudo_random_code(clen, seed)` routine using
  legacy numpy `RandomState` (Mersenne Twister); each transmit
  site uses a distinct integer seed.
- **Chip duration (present):** 10 microseconds (µs) → null-to-null
  spectral width ≈ 100 kilohertz (kHz).
- **Chip duration (planned):** Hysell believes 100 kHz "probably
  exceeds the channel capacity much of the time" and intends to
  migrate to 20 µs chips (50 kHz BW).  The code period stays at
  100 ms.
- **PRN length:** 10 000 chips per code period today; 5 000 under
  the planned migration.  One code period = 100 ms either way.
- **Code uniqueness:** every transmitter emits a distinct PRN
  sequence (seed 0 → Poker, 1 → Gakona, 2 → Palmer).  The
  receiver discriminates co-band transmitters by correlating
  against each one's distinct replica.

### 3.2.1 Daytime D-region absorption

Hysell notes (2026-05-29) that D-region absorption nearly kills
the 2.9/3.4 MHz signals during the day, and at high northern
latitudes this time of year there is effectively no night.
Reception expectations should be set accordingly when validating
new receive sites in summer.

### 3.3 Antennas and radiated power

- Each transmit site uses **two inverted-V dipole antennas**
  aligned northwest to southeast.
- Continuous power per antenna: **0.5 watts (W)**.
- The geometry and modest power are deliberate: the system is
  designed to deliver a sky-wave signal usable for ionospheric
  diagnostics, not for communication, and the low duty-cycle
  spectral footprint avoids interference with conventional HF
  users.

### 3.4 Timing

The transmit clocks at every site are disciplined by the
Global Positioning System (GPS).  This anchors both:

- the start time of every 100-ms PRN code period (so that the
  receiver knows the absolute phase reference); and
- the carrier frequency itself (so that the receiver's measured
  Doppler shift reflects ionospheric motion, not transmitter
  drift).

Sub-microsecond timing accuracy across the network is the
essential ingredient that makes the absolute pseudorange
observable scientifically meaningful.

### 3.5 Extensibility

The same waveform family is in principle deployable at additional
sites — Aricoche & Hysell (2024) noted an Alaska-region deployment
as future work, which is now realised as the three Alaska
transmitters in §3.1.  Adding further transmitters to `hf-tec`
is a configuration-only change: append an entry to
`data/stations.toml` (including the per-site `prn_seed`), add the
new site to the `transmitters.enabled` list in the recorder
config, and reload.  No code change is needed — the PRN generator
already accepts any integer seed.

## 4. Receive architecture

In the Peru deployment, the receive network consisted of six
fixed Cornell- and JRO-administered sites (Jicamarca, Huancayo,
Mala, La Merced, Barranca, Oroya — Aricoche & Hysell 2024 Table
1) that fed the regional ionospheric inversion.  In the North
American re-deployment, the receive network is **not centrally
administered**: any sigmond station with appropriate HF
reception infrastructure can run `hf-tec` and contribute
observations.  This client is the software equivalent of one
such receive site, built to run on commodity software-defined-
radio hardware with the sigmond suite.

### 4.1 Hardware tier

The receive-side hardware is anything `ka9q-radio` supports.  A
typical configuration on a sigmond host:

- one wideband HF antenna (broadband dipole or active loop);
- one low-noise amplifier (LNA);
- one front-end with at least 1 mega-sample-per-second (MS/s)
  in-phase / quadrature (I/Q) capability (e.g. an RX888 MkII
  direct-sampling receiver), with timing locked to a
  GPS-disciplined oscillator (GPSDO) or another stable
  reference;
- `ka9q-radio`'s `radiod` daemon running on the host,
  configured with one channel per transmit frequency to be
  monitored.

Hysell 2018 §2 specifies the production receiver as sampling at
10 mega-samples-per-second (MS/s) at an intermediate frequency
≈3.18 MHz (the midpoint between the two carriers), then digitally
down-converting and decimating in two stages — first to 1 MS/s
across the whole band, then per-carrier to 100
kilo-samples-per-second (kS/s) I/Q baseband for each of the two
frequency channels.  Two carriers in, two narrow baseband streams
out.

`hf-tec` reaches the same per-carrier baseband streams a
stage earlier, by letting `radiod` (ka9q-radio) own all the
down-conversion and decimation — one `radiod` channel per
transmit frequency, each delivering 100 kS/s complex I/Q
(Nyquist ±50 kHz, just wide enough to span the PRN waveform's
≈100 kHz null-to-null bandwidth).  The architectures are
equivalent:

```
Hysell 2018 §2 reference pipeline:

    RF (2.7–3.7 MHz band)
      │
      ▼  ADC @ 10 MS/s, IF ≈ 3.18 MHz
      │  (one wide ADC stream capturing both carriers + the gap)
      │
      ▼  digital DDC + decimate to 1 MS/s
      │
      ▼  per-carrier DDC + decimate to 100 kS/s
      │
      ├──► 2.9 MHz baseband, 100 kS/s I/Q
      └──► 3.4 MHz baseband, 100 kS/s I/Q

hf-tec via ka9q-radio:

    RF (RX888 sees the whole HF spectrum)
      │
      ▼  ADC @ 129.6 MS/s (radiod's direct-sample front-end)
      │
      ▼  radiod's per-channel DDC + decimate
      │
      ├──► 2.9 MHz channel: 100 kS/s I/Q  ─── one ka9q-python MultiStream
      └──► 3.4 MHz channel: 100 kS/s I/Q  ─── one ka9q-python MultiStream
```

The dataflow into the correlator / autocorrelator is identical:
each carrier's PRN-bearing signal arrives as 100 kS/s complex
I/Q at baseband.  A single wideband ≈3.18 MHz channel would
have to be ~1 MS/s wide to span both carriers (rather than ~100
kS/s), and the per-carrier down-conversion would then have to
be reimplemented in Python — duplicating work that radiod
already does natively for free.  The peer recorders
(`codar-sounder`, `hfdl-recorder`) split into per-band channels
for the same reason.

### 4.2 Software pipeline

The software pipeline is one `systemd` instance per `radiod`,
with one in-process pipeline per transmit frequency:

```
radiod (ka9q-radio, IQ preset, 100 kS/s I/Q per channel)
   │
   ▼
HfTecSource (core/stream.py)
   │   ka9q-python MultiStream subscription
   │   frames I/Q into 100 ms (10,000-sample) blocks at the
   │   transmit code-period boundary, anchored to Real-time
   │   Transport Protocol (RTP) timestamps → Coordinated
   │   Universal Time (UTC).
   ▼
ReplicaBank + correlate_bank (core/correlate.py)
   │   one PRN replica per known transmitter, pre-computed
   │   fast-Fourier-transform- (FFT-) of-replica cached.  Per
   │   frame, one FFT(rx) × replica_FFT_conj * IFFT yields the
   │   complex range profile per transmitter.
   ▼
CoherentStack (core/coherent.py)
   │   stacks 100 successive complex range profiles (10 s
   │   coherent integration).  Slow-time FFT across the stack
   │   yields a range-Doppler matrix per transmitter.
   │   Doppler resolution = 1 / 10 s = 0.1 hertz (Hz).
   │   Doppler ambiguity = 1 / 100 ms = ±5 Hz.
   ▼
IncoherentAccumulator (core/coherent.py)
   │   averages six |range-Doppler|² matrices over 1 minute,
   │   matching Hysell 2018's 1-minute cadence.
   ▼
first_hop_detection (core/detect.py)
   │   walks the range axis outward from the configured
   │   minimum and returns the first bin where the peak
   │   Doppler-spectrum power exceeds the configured
   │   signal-to-noise ratio (SNR) gate above the estimated
   │   incoherent noise floor.  Reports first-hop pseudorange,
   │   Doppler 1st moment, amplitude in decibels (dB), and
   │   lock quality.
   ▼
OutputSink (core/output.py)
   │   one JSON Lines (JSONL) record per (transmitter,
   │   receiver, frequency) per minute, daily-rotated.
   │   Additive insert into sigmond's HamSCI SQLite sink at
   │   /var/lib/sigmond/sink.db (table hf_tec.spots).
```

Full DSP-level detail of every stage is in
[`docs/RECEIVER.md`](RECEIVER.md).

### 4.3 Operating modes — locked vs code-free

The pipeline above describes **locked** mode, which requires the
per-transmitter PRN code.  Hysell's per-station generator landed
on 2026-05-29 (see §3.2), so locked mode is now the default.  A
**code-free** mode remains available — useful for first-light
beacon-presence verification before committing to locked-mode
records, and as a stub-regression fallback under `auto`:

- **Codeless detector** (`core/detect_codeless.py`) — for each
  100 kS/s channel, accumulates a configurable integration
  window (default 60 s) and computes the lagged autocorrelation
  at lag τ = 100 ms (= one PRN code period).  Because every
  PRN-coded beacon of this family repeats its waveform every
  100 ms by construction, the autocorrelation magnitude
  |r(τ=100 ms)| rises above the noise floor whenever any
  PRN-coded beacon is present on the channel.
- **Signal-presence test** — comparing |r(τ=100 ms)| to a
  reference autocorrelation at a non-code-period lag yields a
  dB ratio; values above the configured threshold (default 6 dB)
  count as detection.
- **Doppler shift** — the *phase* of r(τ=100 ms) rotates at
  −2π · f_d · τ, so the autocorrelation also recovers the
  Doppler shift directly, without code knowledge.

The codeless mode produces fewer scientific observables than
locked mode: no per-transmitter identification (every PRN code
shares the 100-ms periodicity, so co-band transmitters cannot be
separated) and no absolute pseudorange (no code-phase reference).
What it does deliver is a continuous propagation-state time
series — *"is a beacon reaching this receiver right now, and at
what Doppler shift?"* — which is the right first-light diagnostic
for a station coming on-air, and a useful diurnal-propagation
monitor in its own right.

Mode selection is controlled by `[mode] mode` in the recorder
config: `auto` (the default — resolves to `locked` whenever the
real Hysell generator is wired in, which it now is; falls back
to code-free only if a future regression re-stubs the
generator), `codeless` (always code-free), or `locked` (always
full correlator; requires every enabled Tx to have a `prn_seed`
in `data/stations.toml`).  The contract surface
(`inventory --json`) reports both the configured and resolved
mode per instance.

### 4.4 Why this maps cleanly onto sigmond

The pipeline matches the conventions of the existing sigmond
recorder clients (`psk-recorder`, `wspr-recorder`,
`hfdl-recorder`, `codar-sounder`):

- one `systemd` template instance per `radiod`;
- I/Q ingest via `ka9q-python` with explicit filter-edge
  overrides on `ensure_channel()` to avoid the audio-filter
  default that would clip the PRN bandwidth;
- canonical JSON-Lines output rotated daily;
- additive HamSCI sink (`/var/lib/sigmond/sink.db`) keyed by
  a per-client table;
- contract surface (`inventory --json`, `validate --json`,
  `version --json`) at the sigmond client-contract version
  0.7 specification.

## 5. Data products

Each operating mode (§4.3) writes its own per-minute record
type, to its own JSONL stream, and into its own HamSCI sink
table.  The two schemas are deliberately disjoint to keep them
distinguishable to downstream consumers.

### 5.1 Locked-mode record fields (`mode = "locked"`)

Emitted by the full PRN correlator pipeline (one record per
detected first-hop propagation path per minute).

| Field                 | Meaning |
|-----------------------|---------|
| `time`                | UTC timestamp at the end of the 1-minute incoherent window. |
| `mode`                | `"locked"`. |
| `tx_id`               | Identifier of the transmitter that produced the detected PRN. |
| `rx_id`               | This receiver's `[station] station_id` from the config. |
| `radiod_id`           | The `radiod` instance this recorder is bound to. |
| `frequency_hz`        | Carrier frequency (2.9e6 or 3.4e6). |
| `pseudorange_km`      | Group-path delay in kilometres (km) = group-delay × speed of light / 2. First-hop X-mode per Hysell 2018 §2. |
| `doppler_hz`          | Doppler shift in Hz, computed as the first moment of the Doppler spectrum at the first-hop range bin. |
| `amplitude_db`        | Peak power in the first-hop bin, in decibels (dB) above the estimated noise floor. |
| `snr_db`              | First-hop peak SNR in dB. |
| `noise_floor_db`      | Estimated incoherent noise floor (dB). |
| `lock_quality`        | Heuristic 0–1; higher = sharper, more isolated peak. |
| `range_bin`           | Range bin index used to compute pseudorange. |
| `n_hops`              | 1 (first-hop only at v0.1.0). |
| `processing_version`  | `hf-tec` software version. |
| `contract_version`    | `0.8`. |

### 5.2 Codeless-mode record fields (`mode = "codeless"`)

Emitted by the 100-ms-autocorrelation detector (one record per
frequency per integration window, default 60 s).

| Field                       | Meaning |
|-----------------------------|---------|
| `time`                      | UTC timestamp at the end of the integration window. |
| `mode`                      | `"codeless"`. |
| `rx_id`                     | This receiver's `[station] station_id` from the config. |
| `radiod_id`                 | The `radiod` instance this recorder is bound to. |
| `frequency_hz`              | Carrier frequency. |
| `integration_seconds`       | Length of the integration window. |
| `n_samples`                 | Sample count entering the autocorrelation. |
| `autocorr_magnitude`        | \|r(τ = 100 ms)\| — signal-power fraction of total received power. Range [0, 1]. |
| `autocorr_floor`            | \|r(τ = 137 ms)\| — reference autocorrelation at a non-code-period lag (noise-floor estimate). |
| `autocorr_db`               | 20 · log10(`autocorr_magnitude` / `autocorr_floor`) — the detection statistic. |
| `autocorr_phase_rad`        | arg r(τ = 100 ms) — used to derive Doppler shift. |
| `doppler_hz`                | Doppler shift recovered from autocorrelation phase (±5 Hz unambiguous span). |
| `snr_estimate_db`           | 10 · log10(\|r\| / (1 − \|r\|)) — rough signal-to-noise estimate. |
| `band_power_db`             | Uncalibrated band power for the integration window (dB). |
| `detection`                 | Boolean — `true` when `autocorr_db ≥ detection_threshold_db`. |
| `detection_threshold_db`    | Threshold the detection boolean was evaluated against. |
| `processing_version`        | `hf-tec` software version. |
| `contract_version`          | `0.8`. |

No `tx_id` or `pseudorange_km` — those require code knowledge.

### 5.3 Data sinks

- **JSONL on disk.**  Canonical, append-only, daily-rotated;
  separate spool per operating mode:
  `/var/lib/hf-tec/<radiod_id>/locked/YYYY/MM/DD.jsonl`
  and `…/codeless/YYYY/MM/DD.jsonl`.  One file per UTC day per
  mode; one record per line; never deleted by the client.
- **HamSCI sink (SQLite).**  Additive, on hosts where
  sigmond's local sink at `/var/lib/sigmond/sink.db` is
  writable.  Locked records go to `hf_tec.spots`; codeless
  records go to `hf_tec_codeless.spots`.
- **`.out.mod` legacy text format.**  Not yet implemented.
  Hysell's regional-inversion C code consumes a tab-separated
  text format with a six-line header; an opt-in writer for
  this format is a planned addition so locked-mode records can
  be fed directly into the upstream inversion pipeline.

## 6. Service to HamSCI objectives

The Ham Radio Science Citizen Investigation effort is built on
the premise that the amateur-radio community can contribute to
ionospheric and space-weather science through distributed
observation of HF radio propagation.  Existing HamSCI assets in
the sigmond ecosystem are oriented around:

- spot-based propagation observation (`wspr-recorder` for the
  Weak Signal Propagation Reporter, `psk-recorder` for FT8 /
  FT4 traffic decoded for the PSK Reporter network);
- continuous time-and-frequency standard reception
  (`hf-timestd` for the WWV / WWVH / CHU / BPM broadcasts);
- opportunistic HF aviation data link reception
  (`hfdl-recorder`);
- opportunistic ionospheric sounding from CODAR coastal-radar
  transmissions (`codar-sounder`);
- magnetometer-based geomagnetic activity monitoring
  (`mag-recorder`).

`hf-tec` extends this collection along two axes that the
existing clients do not cover.

### 6.1 Active, deliberately-engineered beacon reception

Existing sigmond clients are mostly **opportunistic** — they
consume whatever signal a third party happens to be radiating
for non-scientific reasons (amateur QSOs, aviation telemetry,
oceanographic radar, time-standard broadcasts).  `hf-tec`
is different: it receives a signal that was **deliberately
engineered for ionospheric science**.  The PRN waveform's
correlation gain (60 dB, derived in §3 above) gives a
detection sensitivity that opportunistic decoders cannot
match, and the GPS-disciplined transmit clock lets the
pseudorange observable be interpreted in absolute terms rather
than as a relative time-difference.

### 6.2 Geographic and observational diversity

The active North American transmit network has three Alaska
sites (two operational, one down for maintenance) and one
planned site (Cornell) — limited geographic spread on the
transmit side.  Receive-side coverage is therefore the variable
that most directly determines what the system can observe.
HamSCI participants running this client add one set of
propagation paths each — every additional receiver between
Alaska / NY and the eventual receive-network edge contributes a
known-geometry oblique path through the ionosphere.  A modest
cluster of HamSCI receivers spread across North America would
cover a much larger ionospheric volume than the transmit-side
endpoints alone.

### 6.3 Direct feed into a published inversion pipeline

The data products are sized to feed Aricoche & Hysell (2024)'s
regional ionospheric inversion directly.  When the planned
`.out.mod` legacy writer lands, HamSCI station observations
can be ingested by the same Cornell analysis pipeline that
produced the regional electron density reconstructions in those
papers.  HamSCI participants thus contribute to a peer-reviewed
science workflow rather than only to a separate amateur-side
dataset.

## 7. Contribution to ionospheric study

The propagation observations `hf-tec` produces inform a
range of ionospheric phenomena that are not well sampled by
either zenith-pointing incoherent-scatter radars (ISRs) or by
the conventional GPS-TEC satellite network.

The Peru deployment that Hysell et al. (2018) and Aricoche &
Hysell (2024) document was tuned to **equatorial** ionospheric
phenomena — equatorial spread F (ESF), the prereversal
enhancement (PRE) of the zonal electric field near sunset,
F-region descent.  Those papers established that the technique
recovers such signatures with spatial resolution that the
zenith Jicamarca Radio Observatory ISR alone could not.  The
North American re-deployment (three Alaska sites — Poker Flat,
Gakona, Palmer — plus Cornell planned, §3.1) positions the
same observable set —
pseudorange and Doppler shift along known oblique paths — to
study an analogous family of **mid- and high-latitude**
phenomena, summarised below.

### 7.1 Storm-time high-latitude ionospheric response

The Alaska transmitters sit between the sub-auroral zone and the
auroral oval.  During
geomagnetic storms the high-latitude F region undergoes
dramatic restructuring: storm-enhanced density plumes, deep
sub-auroral troughs, E-region ionisation enhancements driven
by particle precipitation, ionospheric heating and uplift, and
convective transport of plasma across the polar cap.  HF beacon
receivers in mid- and lower-mid-latitude North America observe
the ionospheric column along each Alaska-to-receiver great
circle, recording pseudorange and Doppler signatures of these
storm-time effects in real time.  The 1-minute cadence matches
the timescale of substorm onset and storm-recovery dynamics.

### 7.2 Sub-auroral trough tracking

The sub-auroral electron-density trough — a sharp minimum that
separates mid-latitude ionospheric structure from the auroral
oval — varies in latitude with solar wind and magnetic activity
and is a primary forecast parameter for HF propagation in high
latitudes.  Beacon paths that cross the trough exhibit
characteristic pseudorange lengthening and amplitude fading at
the trough centre; a distributed receiver network can track the
trough's latitudinal position and motion in near-real time.

### 7.3 Mid-latitude propagation studies (Cornell-anchored)

Once the Cornell transmitter comes on-air, the network gains a
mid-latitude transmit anchor.  The Cornell-to-Alaska paths —
together with receive-side coverage between and beyond — sample
a broad swath of mid- to high-latitude ionosphere.  This
geometry is particularly suited to studying the latitudinal
structure of the F layer, the day-night transition through
high-latitude twilight, and the response of the mid- and
high-latitude ionosphere to solar forcing.

### 7.4 Travelling ionospheric disturbances and gravity-wave signatures

Wavelike fluctuations in pseudorange, optical path length, and
amplitude — at periods of minutes to tens of minutes — are
characteristic of medium-scale travelling ionospheric
disturbances (MSTIDs).  Hysell 2018 §5 notes such fluctuations
in the raw beacon data.  The 1-minute cadence is sufficient to
resolve features at MSTID periods.

### 7.5 Long oblique-path probing

Long oblique paths sample ionospheric volumes that zenith ISR
cannot see and that GPS-TEC samples only along ray paths
constrained by satellite geometry.  Each beacon-link path is
defined by two fixed endpoints with known geometry; this is
geometrically simpler than GNSS occultations and gives a
direct probe of horizontally-stratified ionospheric structure
between the endpoints.

### 7.6 Two-frequency observation per link

Every transmit site radiates 2.9 and 3.4 MHz simultaneously.
For each (transmitter, receiver) pair, the receiver therefore
produces two independent observables (pseudorange × Doppler)
at two frequencies.  Because the index of refraction depends
on frequency, the two-frequency pair separates ionospheric
contributions to group-path delay from those to phase-path
delay, and constrains the F-region peak density independently
from the integrated total electron content along the path.

### 7.7 Continuous, automated, low-maintenance operation

The receiver runs as a `systemd` service, restart-resilient,
with structured logging, contract-conformant `inventory` /
`validate` self-reporting, and standardised on-disk + sink
output formats.  No operator intervention is needed between
configuration and data flowing into the HamSCI sink.  This is
the operational profile that allows a small number of HamSCI
stations to be turned into a continuously-reporting
ionospheric instrument network.

## 8. Open work

Both items that previously blocked locked-mode operation — the
per-station PRN spec and the UTC code-epoch alignment — were
resolved by Hysell on 2026-05-29.  See
[`docs/RECEIVER.md`](RECEIVER.md) §6 for the as-resolved details.
Outstanding items:

- **Cornell PRN seed.**  The Cornell transmitter is planned but
  not yet on-air; its per-site seed will be assigned when it
  comes up.  The pipeline currently logs a warning and skips
  Cornell because its `prn_seed` field is empty.
- **Planned 5 000-chip / 20-µs / 50-kHz migration.**  Hysell
  believes the present 100 kHz bandwidth "probably exceeds the
  channel capacity much of the time" and intends to halve the
  chip rate.  Cutover is a config-only change — see the
  `[processing]` block in the config template for the four
  parameters that must move together.
- **Amplitude calibration reference.**  Hysell 2024 reports
  amplitude in dB without specifying an absolute reference;
  `hf-tec` reports dB above its own noise floor.  This only
  matters when comparing across receivers (the per-receiver
  inversion uses each receiver's own series internally), so it
  is not blocking.
- **Multi-hop returns.**  Currently only first-hop returns are
  detected and reported.  Hysell 2018 explicitly mentions
  multi-hop incorporation as future work with increased
  computational cost.
- **Polarization and arrival angle.**  Each receive site in
  the Peru deployment had two spatially-offset antennas with
  different orientations, permitting polarization and
  interferometric arrival-angle measurement (Hysell 2018 §2).
  HamSCI receivers running this client typically have only one
  antenna, but cross-antenna products would be a useful
  follow-up for stations that do install a second.
- **`.out.mod` writer for direct Hysell inversion feed.**
- **Multi-instance support (sigmond Phase 5+).**  Scaffolded;
  not yet exercised in a multi-instance deployment.

## 9. References

- **Aricoche, J. A. & Hysell, D. L. (2024).**  "Ionospheric
  Radio Beacon Signal Analysis and Parameter Estimation Using
  Automatic Differentiation."  *Journal of Geophysical
  Research: Machine Learning & Computation* 1,
  e2024JH000270.  [doi:10.1029/2024JH000270](https://doi.org/10.1029/2024JH000270).
  Network update for the (then-still-Peru) deployment — 3
  transmit sites, 6 receive sites; amplitude added to the 2018
  pseudorange + Doppler observable set.
- **Hysell, D. L., Baumgarten, Y., Milla, M. A., Valdez, A.,
  & Kuyeng, K. (2018).**  "Ionospheric Specification and
  Space Weather Forecasting with an HF Beacon Network in the
  Peruvian Sector."  *Journal of Geophysical Research: Space
  Physics* 123, 6851–6864.
  [doi:10.1029/2018JA025648](https://doi.org/10.1029/2018JA025648).
  Definitive description of the network, the receive-side DSP
  chain (§2), and the GPS-TEC analog argument that this
  client's name is built around.
- **Hysell, D. L., Milla, M. A., & Vierinen, J. (2016).**  "A
  multistatic HF beacon network for ionospheric specification
  in the Peruvian sector."  *Radio Science* 51, 392–401.
  doi:10.1002/2016RS005951.  Earlier network reference.
- **HamSCI** — [hamsci.org](https://hamsci.org).  Mission and
  objectives of the citizen-science community this client is
  designed to contribute to.
