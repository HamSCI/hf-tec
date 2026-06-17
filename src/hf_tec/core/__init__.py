"""DSP core for hf-tec.

Modules:
  - stream       — RTP I/Q ingest from radiod via ka9q-python.
  - correlate    — PRN replica generation + FFT-based cross-correlation.
  - coherent     — coherent integration → range-Doppler matrices.
  - detect       — first-hop pseudorange, Doppler 1st moment, amplitude.
  - pipeline     — per-frequency orchestrator.
  - daemon       — top-level orchestrator wired to systemd.
  - output       — JSONL + HamSCI sink writers.
"""
