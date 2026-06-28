# hf-tec — Requirements Specification

**Status:** v0.1 baseline (retroactive). **Owner:** Michael Hauan (AC0G).
**Last reconciled against code:** hf-tec `0.1.0` / deploy `0.1.0` (contract v0.8) (2026-06-25).
**Prefix:** `TEC`.

> Application of [sigmond/docs/REQUIREMENTS-TEMPLATE.md](https://github.com/HamSCI/sigmond/blob/main/docs/REQUIREMENTS-TEMPLATE.md)
> at the **Early** end of the maturity range — a v0.1 component whose full DSP
> pipeline is implemented and tested against *synthetic* signals, locked mode is
> wired, but which has not yet caught a real over-the-air beacon. Expect a
> notable share of `🟡`/`⬜` and several `[NEW]`; that is the honest picture and
> the point of the exercise. The sigmond↔component **interface** requirements
> are specified once in the
> [client contract](https://github.com/HamSCI/sigmond/blob/main/docs/CLIENT-CONTRACT.md)
> (v0.8) and referenced — not restated — here (§8.3). Provenance tags:
> `[DOC]` documented · `[CODE]` implicit-in-code · `[NEW]` surfaced by this review.
> Status: ✅ implemented/verified · 🟡 partial/unverified · ⬜ planned.
> *(This client was renamed from `hf-gps-tec` to `hf-tec`; the sink table is
> `hf_tec.spots`.)*

## 1. Context & problem statement

GPS-derived total electron content (GPS-TEC) maps the ionosphere along
near-zenith satellite-to-ground paths. The HamSCI/DASI2 science case wants the
complementary measurement: line-integrated electron density along **oblique HF
propagation paths**, where the ray actually refracts through the F-region. The
HF PRN-coded beacon network of Hysell et al. (2018) and Aricoche & Hysell
(2024) supplies exactly that — a set of low-power (0.5 W/freq), continuous,
GPS-disciplined transmitters emitting two stable carriers (2.9 and 3.4 MHz),
each coded with a per-site pseudorandom-noise (PRN) sequence. A receiver that
correlates against the known codes recovers, per (transmitter, receiver,
frequency) link, the first-hop pseudorange (group delay), Doppler, and
amplitude at 1-minute cadence — the inputs to Hysell's regional inversion.

**hf-tec** is the sigmond-suite **receive site** for that network. It subscribes
to per-frequency wideband I/Q from `radiod` via `ka9q-python`, correlates each
100-ms frame against a bank of PRN replicas, coherently then incoherently
integrates, runs a first-hop detector, and writes per-minute L1 records (JSONL +
an additive shared-sink row). The original Peru network is retired; the current
transmit infrastructure is being re-established in North America under
Dr. David Hysell at Cornell University — three Alaska sites (Poker Flat and
Gakona on-air, Palmer down for a July-2026 maintenance visit) and a planned
fourth at Ithaca, NY. Any sigmond station with HF reception can run this client
and contribute observations; there is no central receiver administration.

v0.1 deliberately establishes a **validated recorder** — a fully structured DSP
chain proven against synthetic signals, with locked mode wired the moment
Hysell supplied the per-station PRN generator (2026-05-29) — ahead of any
real-network detection or absolute-amplitude calibration. The defining v0.1
principle: *get the correlator, integration, and detector demonstrably correct
against a known synthetic truth, and make adding a transmitter a config-only
change, before claiming any geophysical product.*

## 2. Goals & objectives

- **Detect and range** each known HF beacon by full PRN correlation, emitting
  per-(Tx, Rx, freq) first-hop pseudorange, Doppler, and amplitude at the
  Hysell 1-minute cadence.
- Offer a **codeless** first-light mode (100-ms autocorrelation) that confirms
  beacon presence and recovers Doppler without the PRN code.
- Be **upstream-compatible** with Hysell's inversion (`focus.c`) input format —
  the per-minute record is sized to feed it directly.
- Make **network growth a config-only change** (add a `[transmitters.<SITE>]`
  block with a `prn_seed` and enable it; no code edit).
- Run as a well-behaved suite client (one instance per radiod, off radiod cores,
  timing-authority aware, additive shared sink) *and* standalone.
- Be **verifiable**: ship a synthetic-IQ test path and an on-host QA plausibility
  check (geometry-gated detection rates) so a misconfiguration is visible
  without ground truth.

## 3. Non-goals / out of scope

- **Transmitting / operating beacons** — strictly a one-way passive receiver;
  the transmit network is Cornell/Hysell's (Owner: external).
- **Being a receiver front-end** — it consumes pre-tuned wideband RTP from
  `radiod`; it does not tune hardware. (Owner: ka9q-radio.)
- **Ionospheric inversion** — hf-tec produces L1 observables (pseudorange/
  Doppler/amplitude); the electron-density inversion is Hysell's `focus.c`
  / regional-inversion pipeline (Owner: external / downstream analysis).
- **Producing a timing authority** — it *consumes* hf-timestd's §18 authority;
  it does not produce one (that is hf-timestd).
- **Multi-hop / mode separation** — v0.1 is first-hop only; multi-hop is future
  work.
- **Cross-receiver absolute amplitude comparison** — deferred to Phase 2
  (absolute amplitude calibration reference).

## 4. Stakeholders & actors

Station operator · `radiod` (ka9q-radio, wideband IQ source, required) ·
`hf-timestd` (§18 timing-authority producer, optional) · the Cornell/Hysell
transmit network (signal source of opportunity; supplies the PRN spec and per-Tx
seeds) · the shared SQLite sink + downstream science consumers (Hysell inversion,
`focus.c`) · sigmond (multi-instance lifecycle, CPU affinity, status, watch UI) ·
`stations.toml` network topology (Tx geometry + per-Tx PRN seed) ·
`/etc/sigmond/coordination.env` (identity, chain delay, log level).

## 5. Assumptions & constraints

- `TEC-C-001` `[DOC]` ✅ `radiod` SHALL provide a **wideband** IQ channel per Tx
  frequency (default 100 kHz BW, 100 kS/s, ±50 kHz around carrier) via
  ka9q-python `ensure_channel(low_edge, high_edge)` — not an audio-filtered path.
- `TEC-C-002` `[DOC]` ✅ The PRN code SHALL be reproduced with legacy numpy
  `RandomState` (Mersenne Twister), seeded per-site, complex random-phase (not
  real BPSK); `default_rng`/PCG64 would silently desync from the transmitter.
- `TEC-C-003` `[DOC]` ✅ Code epochs SHALL be treated as **100-ms-aligned to UTC**
  (`t_chip0 mod 100 ms = 0`), per the GPS-disciplined network (Hysell 2026-05-29).
- `TEC-C-004` `[CODE]` ✅ One systemd instance SHALL run **per radiod**
  (`hf-tec@<radiod_id>`); the instance name is the reporter-id spool key.
- `TEC-C-005` `[CODE]` ✅ `[processing]` parameters SHALL be mutually consistent
  (`chip_us × code_chips / 1000 = code_period_ms`; `coherent_reps × code_period_ms
  = coherent_seconds × 1000`; `sample_rate_hz` an integer multiple of chip rate),
  or the correlator refuses to start / validate fails.
- `TEC-C-006` `[CODE]` ✅ Python ≥3.11; runtime deps numpy/scipy/ka9q-python;
  `ka9q-python` and `sigmond` are editable siblings (fleet-upgrade pattern).
- `TEC-C-007` `[DOC]` ✅ The receiver SHALL identify itself solely via the
  `[station]` block; receive sites are not centrally administered.

## 6. Functional requirements

### 6.1 Acquisition
- `TEC-F-001` `[DOC]` ✅ SHALL open one ka9q-radio `MultiStream` channel per
  enabled `[[frequency]]` (default 2.9 + 3.4 MHz) and frame it into 100-ms code
  periods (10,000 samples at 100 kS/s) for the pipeline.
- `TEC-F-002` `[DOC]` ✅ SHALL run one independent `FreqPipeline` per enabled
  frequency, all funnelling into a single thread-serialised `OutputSink`.
- `TEC-F-003` `[CODE]` ✅ SHALL self-restart a stalled subscription
  (`stall_timeout_s`, default 30 s, exponential backoff) rather than stall
  silently when radiod stops delivering frames.

### 6.2 Locked-mode detection (the v0.1 core)
- `TEC-F-010` `[DOC]` ✅ SHALL build a `ReplicaBank` — one PRN replica per enabled
  Tx (per `stations.toml` `prn_seed`), with replica FFTs precomputed at startup —
  and correlate each frame against every replica (FFT circular cross-correlation).
- `TEC-F-011` `[DOC]` ✅ SHALL coherently integrate 100 successive code reps
  (10 s) into a per-Tx range-Doppler matrix (slow-time FFT → 0.1 Hz Doppler
  resolution), then incoherently average 6 × 10-s windows into a 1-min record.
- `TEC-F-012` `[DOC]` ✅ SHALL run a **first-hop** detector — first range bin
  above `snr_threshold_db`, bounded by `min/max_pseudorange_km` — emitting
  `pseudorange_km`, `doppler_hz` (Doppler first moment), `amplitude_db`,
  `snr_db`, `noise_floor_db`, and a 0–1 `lock_quality` heuristic per Tx.
- `TEC-F-013` `[DOC]` ✅ SHALL skip a Tx that has no `prn_seed` assigned in
  `stations.toml` (e.g. CORNELL) with a logged warning, not a crash.
- `TEC-F-014` `[CODE]` 🟡 The locked-mode pipeline is verified against
  **synthetic** signals only; it has **not** detected a real over-the-air beacon
  (no Cornell/Alaska seed confirmation captured live). *(gap — `TEC-F-091`.)*
- `TEC-F-015` `[CODE]` ✅ The PRN generator SHALL match Hysell's reference
  sequence bit-for-bit, guarded by `test_generator_matches_hysell_reference`;
  a stub regression SHALL be detected via the `PRN_IS_STUB` flag and surfaced.

### 6.3 Codeless mode (first-light)
- `TEC-F-020` `[DOC]` ✅ With mode `codeless`, SHALL run a 100-ms-autocorrelation
  detector (`codeless_integration_seconds`, default 60 s) that confirms beacon
  presence and recovers Doppler **without** the PRN code (no per-Tx pseudorange).
- `TEC-F-021` `[DOC]` ✅ Mode resolution: `auto` SHALL resolve to `locked` when a
  real PRN spec is wired (current), `codeless` when the generator is stubbed;
  `codeless`/`locked` pin the mode; `inventory` reports `mode_configured` /
  `mode_resolved`.

### 6.4 Output
- `TEC-F-030` `[DOC]` ✅ SHALL write one canonical JSONL record per (Tx, Rx, freq)
  per minute, daily-rotated at UTC midnight under
  `/var/lib/hf-tec/<instance>/{locked,codeless}/YYYY/MM/DD.jsonl`.
- `TEC-F-031` `[DOC]` ✅ SHALL additively write a projected row to the shared sink
  — `hf_tec.spots` (locked) / `hf_tec_codeless.spots` (codeless) — as a silent
  no-op when `/var/lib/sigmond/sink.db` is unwritable (JSONL stays canonical).
- `TEC-F-032` `[CODE]` ✅ Every record SHALL carry `reporter_id`,
  `processing_version`, and `contract_version` provenance.
- `TEC-F-033` `[DOC]` ⬜ An opt-in `.out.mod` text writer (Hysell `focus.c`
  inversion input) SHALL be available; scaffolded (`jro_out_mod=false`) but **not
  implemented**. *(gap — `TEC-F-093`.)*

### 6.5 QA / plausibility self-check
- `TEC-F-040` `[CODE]` ✅ SHALL provide a `qa` subcommand + templated
  `hf-tec-qa@<instance>.timer/.service` that appends a daily QA JSONL row and a
  one-line journal verdict, snapshotting whether detections land in the
  geometry-expected first-hop range bins per Tx (low-priority, never steals CPU).
- `TEC-F-041` `[CODE]` 🟡 The QA verdict is a **plausibility** heuristic
  (geometry-gated detection rates / cross-Tx ratio), not validation against
  ground truth; it can only flag gross misconfiguration. *(documented limit.)*

### 6.6 Self-description & config (contract surface)
- `TEC-F-050` `[CODE]` ✅ SHALL implement `inventory --json` / `validate --json` /
  `version --json` / `status` per contract v0.8 (see §8.3) with pure-JSON stdout.
- `TEC-F-051` `[CODE]` ✅ `validate` SHALL **fail** on no `[[frequency]]` blocks,
  non-positive `chip_microseconds`/`code_chips`, or inconsistent `[processing]`
  arithmetic; and **warn** on empty `[ka9q].status_address`, no enabled
  transmitters, an unknown Tx not in `stations.toml`, a non-integer sample-rate/
  chip-rate multiple, or a stubbed PRN generator.
- `TEC-F-052` `[CODE]` 🟡 SHALL provide `config show` / `config apply`; the full
  contract §14 `config init|edit` **wizard** (`sigmond.wizard_dispatch`) is
  deferred — operator hand-edits the TOML. *(gap — `TEC-F-094`.)*

## 7. Quality / non-functional requirements

- `TEC-Q-001` `[CODE]` ✅ The detector SHALL run off radiod's CPU cores (sigmond
  `AFFINITY_UNITS`) so burst processing cannot induce RX888 USB drops.
- `TEC-Q-002` `[CODE]` ✅ The service SHALL be `Type=notify` with `WatchdogSec=180`,
  `Restart=on-failure`, `MemoryMax=512M`/`MemorySwapMax=0` (runaway buffer is
  restarted, not allowed to OOM the host), and a hardened sandbox
  (`ProtectSystem=strict`, scoped `ReadWritePaths`).
- `TEC-Q-003` `[CODE]` ✅ Per-frequency pipeline threads SHALL serialise all
  output through one lock so the SQLite connection is never touched concurrently
  and JSONL day-rotation cannot race.
- `TEC-Q-004` `[CODE]` ✅ Shared-sink writes SHALL degrade to a graceful no-op
  (lazy-import, exception-swallowing) so a sink error never stops the daemon and
  the package stays usable without sigmond.
- `TEC-Q-005` `[CODE]` ✅ Degenerate `[processing]` config SHALL be caught by
  `validate` (well-formed JSON) rather than crashing the daemon at startup
  (no `ZeroDivisionError`).
- `TEC-Q-006` `[NEW]` ⬜ Output SHALL bound disk growth: JSONL has **no retention/
  eviction** policy and `data_sinks.retention_days = 0` / `mb_per_day = 0` are
  placeholders. A real per-day volume estimate + retention policy SHALL be set.
  *(gap — `TEC-Q-090`.)*
- `TEC-Q-007` `[NEW]` 🟡 Amplitude SHALL be reported against a defined reference;
  v0.1 reports dB above its **own** incoherent noise floor, which is not
  comparable across receivers (Phase 2 absolute calibration). *(gap —
  `TEC-Q-091`, ties #18 hf-tec Phase 2.)*

## 8. External interfaces

### 8.1 Inputs
- radiod wideband IQ via ka9q-python — one channel per enabled `[[frequency]]`
  (`center_hz`, `sample_rate_hz`), `[ka9q].status_address` (mDNS), `filter_guard_hz`.
- `/etc/hf-tec/hf-tec-config.toml`. Operator MUST set: `[station].station_id` +
  `latitude_deg`/`longitude_deg` (path-geometry anchor); `[ka9q].status_address`
  (or sigmond supplies it via coordination.env). Optional: `[[frequency]]`,
  `[processing]`, `[transmitters].enabled`, `[mode].mode`, `[sinks]`,
  `[instance].reporter_id`.
- `/etc/hf-tec/data/stations.toml` — Tx geometry + per-Tx `prn_seed` (POKER_FLAT=0,
  GAKONA=1, PALMER=2; CORNELL unassigned). The replica bank is built from this.
- `hf-timestd` §18 authority (`/run/hf-timestd/authority.json`, optional) and
  `/etc/sigmond/coordination.env` (identity, `RADIOD_<id>_CHAIN_DELAY_NS`, log level).

### 8.2 Outputs
- Canonical JSONL: `/var/lib/hf-tec/<instance>/{locked,codeless}/YYYY/MM/DD.jsonl`.
- Shared sink (derived from inventory `data_sinks`): SQLite at
  `/var/lib/sigmond/sink.db`, **target_db/table `hf_tec.spots`** (locked,
  `schema_ref hf_tec.locked.jsonl.v1`) and **`hf_tec_codeless.spots`** (codeless,
  `schema_ref hf_tec.codeless.jsonl.v1`). Locked-row fields: `time`, `reporter_id`,
  `tx_id`, `rx_id`, `radiod_id`, `frequency_hz`, `pseudorange_km`, `doppler_hz`,
  `amplitude_db`, `snr_db`, `noise_floor_db`, `lock_quality`, `range_bin`,
  `n_hops`, `processing_version`, `contract_version`.
- QA: `/var/lib/hf-tec/<instance>/qa/YYYY-MM-DD.jsonl` + journal verdict line.
- Process log: systemd journal (`hf-tec@<id>` / `hf-tec-qa@<id>`).

### 8.3 Contracts / APIs (reference, not restated)
- `TEC-I-001` `[CODE]` ✅ Conforms to **client contract v0.8** (multi-instance);
  `deploy.toml` declares `templated_units=["hf-tec@.service"]`,
  `contract_version=0.8`, `deps.git=[ka9q-python]`, and `[client_features.watch]`
  / `[client_features.receiver_channels]` for the sigmond watch UI + TUI Activity.
  `inventory` declares `data_path.kind=radiod-ka9q-python`,
  `data_sinks=[file, sqlite]` (table per §8.2), `control_socket=/run/hf-tec/control.sock`,
  `frequencies_hz` + `ka9q_channels`, `transmitters_enabled`, `mode_configured/
  mode_resolved`. Full field semantics: contract §3/§16/§17.
- `TEC-I-002` `[DOC]` 🟡 **Timing-authority consumer (capability-only):** reads
  hf-timestd's §18 authority via the shared `hamsci_dsp.timing.AuthorityReader`;
  the frame anchor is derived from the RTP counter + published offset via the
  shared `hamsci_dsp.timing.acquire_anchor_utc` helper (`stream.py:_compute_anchor_utc`,
  METROLOGY §4.5 RTP-reference invariant), never the host clock. But `inventory`
  reports `uses_timing_calibration=false`, `timing_authority_applied=null`:
  the authority is **not yet consumed for absolute code-epoch PRN alignment**.
  Full §18 consumption is Phase 2 (`TEC-F-092`). Subscriber obligations are
  defined by the contract, not here.
- `TEC-I-003` `[DOC]` 🟡 **§14 wizard deferred** — `deploy.toml` leaves
  `[contract.config]` commented; init/edit is operator hand-edit (`TEC-F-052`).

## 9. Data requirements

Canonical JSONL record (per (Tx, Rx, freq) per minute) carries the full L1
detection (locked: pseudorange/Doppler/amplitude/SNR/noise-floor/lock-quality/
range-bin; codeless: autocorr magnitude/floor/phase/Doppler/band-power/detection
flag). Sink row is a flat projection of the same record (§8.2). Schema refs
`hf_tec.locked.jsonl.v1` / `hf_tec.codeless.jsonl.v1`; sink schema `hf_tec.spots`
/ `hf_tec_codeless.spots`. Reference data: `stations.toml` (3 Alaska Tx + 1
planned Cornell). Timing label: UTC at end of the 1-min incoherent window,
RTP-anchored. **Retention/volume:** unset in v0.1 (`retention_days=0`,
`mb_per_day=0` placeholders — see `TEC-Q-090`).

## 10. Dependencies & development sequence

**Deps:** `radiod` (required), `ka9q-python ≥3.14` (editable sibling), `numpy ≥1.24`,
`scipy ≥1.10`; `sigmond` (lazy-import, optional, for the shared sink). Dev extra:
`pytest`/`pytest-asyncio`. Hardware: RX888 via radiod (GPSDO-locked); `hf-timestd`
optional.

**Development sequence (intended, recovered as requirement):**
- **v0.1 scaffolding (current):** full DSP pipeline (correlate → coherent →
  detect → output), validated against **synthetic** signals; codeless first-light
  mode; QA plausibility check + timer; contract v0.8 self-describe surface.
- **Locked mode wired (2026-05-29):** Hysell per-station PRN generator + UTC
  epoch alignment landed; per-Tx seeds (Poker Flat/Gakona/Palmer) assigned;
  `auto` resolves to `locked`. **Remaining:** Cornell Tx seed (unassigned, site
  not on-air) and first real over-the-air detection.
- **Phase 2 (planned, #18 hf-tec epic):** absolute amplitude calibration
  reference (cross-receiver comparability); full §18 timing consumption for
  absolute code-epoch PRN alignment; `.out.mod` writer for direct `focus.c`
  inversion; retention/volume policy.

## 11. Acceptance criteria & verification

- Contract conformance → `hf-tec validate --json` (exit 0, no `fail`) surfaced via
  `smd status`.
- DSP correctness → synthetic-IQ pytest suite (`test_correlate.py`,
  `test_detect`, `test_generator_matches_hysell_reference`) — the v0.1 acceptance
  hinge, since no real-network ground truth exists yet.
- Locked vs codeless → `inventory` `mode_configured`/`mode_resolved`;
  PRN-stub regression flagged by `PRN_IS_STUB` warning in `validate`/`inventory`.
- Plausibility in production → `hf-tec qa` verdict + `hf-tec-qa@*.timer` daily row
  (geometry-gated detection-rate check).
- Sink/JSONL integrity → record schema stability + graceful no-op when sink absent.
- Instance isolation → one `hf-tec@<radiod>` per radiod, off radiod cores,
  watchdog healthy.

## 12. Risks & open questions

- `TEC-F-091` `[NEW]` 🟡 **No real-network detection yet:** locked mode is verified
  on synthetic signals only; no live Alaska/Cornell beacon has been caught.
  Gates any geophysical claim. *(candidate #18 hf-tec issue.)*
- `TEC-F-092` `[NEW]` ⬜ **Timing authority read-but-not-consumed:** §18 is RTP-
  anchored but not applied to absolute code-epoch PRN alignment
  (`uses_timing_calibration=false`). Phase 2 must close this. *(#18 hf-tec Phase 2.)*
- `TEC-F-093` `[NEW]` ⬜ **No Cornell Tx seed:** CORNELL has no `prn_seed`; the
  pipeline skips it with a warning. Assign when Hysell publishes it and the site
  comes on-air. *(#18 hf-tec Phase 2 — Cornell Tx seed.)*
- `TEC-F-094` `[NEW]` 🟡 **`.out.mod` writer not implemented:** the `focus.c`
  inversion-input flavour is scaffolded (`jro_out_mod=false`) but unwritten.
- `TEC-F-095` `[NEW]` ⬜ **§14 config wizard deferred:** only `config show/apply`
  exist; operator hand-edits the TOML. Promote to the contract-standard wizard.
- `TEC-Q-090` `[NEW]` ⬜ **No retention/volume policy:** JSONL grows unbounded and
  `mb_per_day`/`retention_days` are 0 placeholders; set a real estimate + policy.
- `TEC-Q-091` `[NEW]` 🟡 **Amplitude not absolutely calibrated:** dB-above-own-
  noise-floor is not cross-receiver comparable (Phase 2 amplitude calibration).
  *(#18 hf-tec Phase 2 — amplitude calibration.)*
- **Doc drift:** `CLAUDE.md` text says contract v0.7 in places, but `contract.py`
  and `deploy.toml` declare **v0.8** — align the prose. The repo README still
  frames status as "scaffolding" though locked mode is wired; reconcile wording.

## 13. Traceability

| Requirement | #18 issue | Verification | PSWS #6 |
|---|---|---|---|
| TEC (overall Phase 2) | Clients: hf-tec — Phase 2 | — | #6:19 (Doppler API) |
| TEC-F-010/011/012 (locked detect) | Clients: hf-tec | synthetic-IQ pytest | #6:31 (sensor integ.) |
| TEC-F-091 (real detection) | *(new — file)* | live capture vs stations geometry | — |
| TEC-F-092 (§18 consumption) | hf-tec: absolute code-epoch PRN alignment | timing test | #6:50 |
| TEC-F-093 (Cornell Tx seed) | hf-tec Phase 2 — Cornell Tx seed | replica-bank build | — |
| TEC-Q-091 (amplitude calibration) | hf-tec Phase 2 — amplitude calibration | cross-Rx compare | — |
| TEC-F-031 (sink hf_tec.spots) | Clients: hf-tec | sink schema test | #6:31 (sensor integ.) |
| TEC-Q-090 (retention/volume) | *(new — file)* | data-summary review | — |

*New rows (TEC-F-091/093/094/095, TEC-Q-090/091) are this review's surfaced gaps;
TEC-F-092/093 and TEC-Q-091 promote to the #18 hf-tec Phase-2 epic (§18
consumption, Cornell Tx seed, amplitude calibration).*
