# Spike 8c: localized DFT-spread OFDM

This directory contains the offline-only Spike 8c preregistration,
implementation, deterministic tests, and lightweight results. The experiment
compares exactly one localized DFT-spread OFDM mapping with the accepted Cyrinx
2 conventional-OFDM geometry. It does not modify the shipping modem and does
not authorize live audio on its own.

The deciding contract is frozen in
[`preregistration.json`](preregistration.json). In particular, pilot placement,
normalization, seeds, cells, held-out split, and gates must not change after an
outcome is observed. A failed gate is a result, not permission to tune this
candidate in place.

Run the deterministic checks and complete campaign from the repository root:

```bash
.venv/bin/python scratch/spikes/dfts_ofdm_8c/test_spike.py
.venv/bin/python scratch/spikes/dfts_ofdm_8c/spike.py run
```
