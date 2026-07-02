# cyrinx.org

Static site for the Cyrinx acoustic-modem research project (ROADMAP Track C).
No build step, no framework, no third-party requests: plain HTML/CSS/JS with
self-hosted IBM Plex woff2 subsets. The hero synthesizes an actual bulk-PHY
frame (chirp → guard → sync → OFDM, real geometry: 48 kHz, NFFT 2048, CP 768,
bins 47–981) in the browser, renders its spectrogram, and can play it via
WebAudio (user gesture only).

## Local preview

```bash
python3 -m http.server -d site 8080
# open http://localhost:8080
```

(`file://` won't load fonts/JS cleanly in all browsers; use the server.)

## Deploy (Cloudflare Pages — user-gated, needs wrangler auth on cyrinx.org)

```bash
npx wrangler pages deploy site --project-name cyrinx
```

or connect the repo in the Cloudflare dashboard with build output directory
`site` and no build command. `_headers` carries cache/security headers.

## Regenerating assets

- `assets/og-card.png` + `assets/apple-touch-icon.png`:
  `.venv/bin/python3 scripts/gen-site-assets.py` (renders a real frame
  spectrogram via `scratch/hw20k/modem.py`; needs matplotlib in the venv).
- `cyrinx-acoustic-link.pdf` is a copy of `docs/whitepaper/`'s compiled PDF —
  re-copy when the paper changes.
- Fonts: latin woff2 subsets of IBM Plex (OFL), fetched from Google Fonts.

## Note until Phase 5

The GitHub links on the page 404 for the public until the repo is flipped
public (PUBLICATION.md Phase 5). Deploy after (or with) the flip.
