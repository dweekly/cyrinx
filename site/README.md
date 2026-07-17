# cyrinx.org

Static site for the Cyrinx acoustic-modem research project (ROADMAP Track C).
No build step, no framework, no third-party requests: plain HTML/CSS/JS with
self-hosted IBM Plex woff2 subsets. The hero synthesizes a short deterministic
geometry illustration patterned after the original control profile (chirp →
guard → ten random-QPSK OFDM symbols; 48 kHz, NFFT 2048, CP 768, bins 47–981),
renders its spectrogram, and can play it via WebAudio (user gesture only). It
does not implement the modem's sync, pilots, payload mapping, FEC, or CRC.

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
  The checked-in card was regenerated and visually inspected with the 65.875
  kbps schedule-comparable Cyrinx 2.0 copy. Social-image metadata remains
  intentionally absent unless a deployment change explicitly enables it.
- `cyrinx-acoustic-link.pdf` is the 28-page Cyrinx 1.0 paper and
  `cyrinx-2-goodput.pdf` is the separate 11-page Cyrinx 2.0 follow-on. Both are
  copies of compiled PDFs under `docs/whitepaper/`; re-copy either when its
  source changes.
- Fonts: latin woff2 subsets of IBM Plex (OFL), fetched from Google Fonts.

## Publication status

The repository, v2.0.0 release, and Cyrinx 2.0 site are public. Future changes
in `site/` do not update the live site until an authenticated Cloudflare
deployment is run.
