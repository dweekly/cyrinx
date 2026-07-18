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

## Result

The complete preregistered campaign ran 10,000 frames, including 8,000 held-
out frames. The candidate reduced held-out q99.9 pre-limiter crest factor by
2.7775 dB, missing the frozen 3.0 dB gate. The coded-RIR and spectral gates
passed, but the conjunction did not; OTA and library integration are not
permitted by this spike.

See [`results/REPORT.md`](results/REPORT.md) for the quantitative result and
[`NEXT_EXPERIMENT.md`](NEXT_EXPERIMENT.md) for the conditional minimum follow-
up. The latter deliberately does not tune the failed candidate in place.
