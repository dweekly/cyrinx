# Cyrinx whitepapers

Fresh as of 2026-07-17. The publication record contains two separate papers:

- `cyrinx-acoustic-link.tex` / `.pdf` (28 pages): the Cyrinx 1.0 system paper,
  historical bidirectional measurements, channel/defect study, robustness
  supplement, and accounting errata.
- `cyrinx-2-goodput.tex` / `.pdf` (11 pages): the Pixel-specific Cyrinx 2.0
  follow-on, including the 65.875 kbps schedule-comparable campaign, receiver
  replay, zero-gap frontier, rejected branches, and limitations.

Build either paper from this directory with two LaTeX passes:

```bash
pdflatex -interaction=nonstopmode -halt-on-error cyrinx-acoustic-link.tex
pdflatex -interaction=nonstopmode -halt-on-error cyrinx-acoustic-link.tex
pdflatex -interaction=nonstopmode -halt-on-error cyrinx-2-goodput.tex
pdflatex -interaction=nonstopmode -halt-on-error cyrinx-2-goodput.tex
```

Regenerate the Cyrinx 2.0 figures from tracked evidence, without hardware or
raw audio, from the repository root:

```bash
.venv/bin/python scripts/gen-cyrinx2-paper-figures.py
```

The generated PNGs are retained for visual review and the PDFs are embedded in
the follow-on paper. Full campaign manifests, raw captures, frozen binaries,
and detailed replay reports remain ignored local artifacts; the tracked ledger
and hashes authenticate them but do not make the raw experiment publicly
reconstructable.
