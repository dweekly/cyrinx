# A1 — Auto-MRC + decode-based mic selection in the live adaptive loop

Fresh as of 2026-06-13. Execution plan for **Track A1** of
[../ROADMAP.md](../ROADMAP.md). **Not yet started** — written down for a
later session. Companion to [PUBLICATION.md](PUBLICATION.md) and
[EXPERIMENTS.md](EXPERIMENTS.md).

## Context

The #1 gap from [../ROADMAP.md](../ROADMAP.md): the robustness wins (two-mic
maximal-ratio combining, decode-based mic selection) live only in the Python
reference (`scratch/hw20k/modem.py`) and bench drivers, while the live adaptive
loop (`scratch/hw20k/adaptive.py`) decodes coherent OFDM **single-mic** via the C
library. The paper's §Robustness describes MRC rescuing cells where neither mic
decodes alone (measured at `edge_below_laptop` — mic0 0/11, mic1 0/11, MRC 8/11;
see `scratch/hw20k/mrc_validate.py:9-12`). A1 wires that into the loop's coherent
decode so the adaptive link actually exhibits the graceful degradation the paper
claims. It is the bounded step before A2 (porting diversity into the C codec).

**Current state (verified by reading the code):**
- `adaptive.py:66-68` captures stereo but discards one mic (`st[:, mic]`) and
  decodes via `clib.decode` (single-channel C API — no `rx2` exists in C).
- `modem.demodulate_frame(cfg, rx, rx2=...)` (`modem.py:416-502`) has the full
  validated per-subcarrier MRC.
- **Unverified hypothesis:** a `clib.encode`-produced frame is bit-compatible
  with `modem.demodulate_frame`. `mrc_validate.py` used Python TX, not clib TX.
  The C codec is a port of the Python reference (clib.py docstring, PUBLICATION
  PR 1.10), so this *should* hold — but must be proven digitally first (Step 1).

## Approach: clib-first, MRC escalation

Keep the library-native single-mic clib decode as the primary path (preserves the
"library-native goodput" framing of the headline numbers), and **escalate to
Python MRC when the clib decode is imperfect** (decode fails or
`blocks_ok < blocks_total`). Record both results per rep in the JSONL so the
evidence shows exactly what single-mic delivered vs. what MRC rescued. This
demonstrates decode-based selection (the sounder already picks the probe-best mic,
`sounder.py:266-272`) *plus* combining, without giving up library-native numbers
in clean cells.

## Work plan (one PR)

**Worktree:** `~/dev/cyrinx-WORKTREE/a1-auto-mrc`, branch `robustness/a1-auto-mrc`,
tracking PR opened with the first commit (the spike). Work in `scratch/hw20k/`;
venv at `.venv` (clib needs only numpy).

### Step 1 — Cross-compat spike (validate before integrating)
New diagnostic `scratch/hw20k/xcompat_validate.py` (kept in-repo per
ship-the-spike policy):
- Build matching configs: `clib.make_cfg(...)` and a bridge helper
  `modem_cfg_from_clib(cfg)` → `modem.Config(f_lo, f_hi, pilot_every=8,
  bits_per_bin={b: bpb for b in data_idx}, rate, n_sym, nfft, cp, sr, amp,
  clip_sigma)` (fields map 1:1, `modem.py:227-269` vs `clib.py:24-57`; chirp
  defaults already agree at 2000/16000 Hz).
- Digital loopback: `clib.encode(payload)` + leading/trailing silence →
  (a) `modem.demodulate_frame` mono — payload & all blocks must match;
  (b) `rx2=` a copy with independent AWGN — MRC path must decode;
  (c) sanity: one channel heavily corrupted — MRC still decodes (null-fill).
- Run across the MCS/CP grid the loop can emit: (4,"3/4"), (4,"1/2"), (2,"1/2")
  × CP {768, adaptive long-CP values, e.g. 3072 / nfft 4096}.
- **Contingency:** if any grid cell mismatches, diagnose the divergence (likely
  interleaver / sync-PRNG / CRC framing) and report before proceeding — do not
  silently fall back.

### Step 2 — Wire escalation into `adaptive.py`
In the coherent branch (`adaptive.py:58-79`):
- Put the bridge helper in `clib.py` (next to `Cfg`) as `modem_cfg_from_clib(cfg)`;
  the spike imports it from there.
- Per rep: clib decode on the sounder-selected mic (unchanged). If `d is None` or
  `blocks_ok < blocks_total`, run `modem.demodulate_frame(mcfg, st[:,0],
  rx2=st[:,1])` and take the better result (more `blocks_ok`; payload-verified
  blocks only, same as today).
- Track per-rep provenance: `{clib_ok, mrc_ok, used}`; JSONL row gains
  `mrc_rescued_blocks`, `decode_path` fields. Console line shows e.g.
  `OFDM 16-QAM r3/4: 10/12 clib + 2/12 MRC-rescued -> 31.4 kbps`.
- Goodput definition unchanged (CRC-valid bits / airtime).

### Step 3 — Offline selftest for the new path
`adaptive.py selftest` (no hardware): synthesize a stereo capture from
`clib.encode` digitally (mic0 = clean+noise, mic1 = clean+different noise; plus a
case with mic0 nulled so single-mic fails), stub `send`, assert the escalation
logic picks the right path and decodes. Closes the "add the auto-MRC path to unit
coverage" hygiene item from ../ROADMAP.md.

### Step 4 — OTA re-validation across the orientation set *(needs bench)*
Requires Pixel 7a on USB (`adb`) and physical placement — **user at the bench**.
Gain staging per the README bench quick-start (Mac out 100, in 22, phone media
max — M4-specific; re-derive on any other Mac).
- `python scratch/hw20k/harness.py smoke` first.
- `adaptive.py <label>` at the known cells: clean (`port_fnkey`-style, expect
  clib-only, ~39-48 kbps, MRC never invoked), reverberant keyboard-well (expect
  long-CP + occasional rescue), shadowed `edge_below_laptop` (expect MRC to
  rescue blocks where single-mic fails).
- Append results to `data/adaptive_demo.jsonl`; summarize in
  [EXPERIMENTS.md](EXPERIMENTS.md).

### Step 5 — Docs
- [../ROADMAP.md](../ROADMAP.md): strike A1; [../CHANGELOG.md](../CHANGELOG.md):
  record the milestone.
- `scratch/hw20k/NOTES.md`: one-liners for `xcompat_validate.py` and the new
  adaptive.py behavior (partial payment on the "NOTES index" hygiene item).
- [EXPERIMENTS.md](EXPERIMENTS.md): the re-validation table.
- Whitepaper: no edit required (it already describes MRC); if the OTA sweep
  produces a notably better degraded-cell number, note it as a candidate for a
  one-line update — separate decision, not in this PR.

## Files touched
- `scratch/hw20k/adaptive.py` (~30 lines: escalation + reporting + selftest)
- `scratch/hw20k/clib.py` (+`modem_cfg_from_clib`, ~15 lines)
- `scratch/hw20k/xcompat_validate.py` (new spike, ~80 lines)
- `scratch/hw20k/NOTES.md`, `ROADMAP.md`, `CHANGELOG.md`, `docs/EXPERIMENTS.md`
- No C/Swift changes (that's A2). No golden-vector changes.

## Verification
1. `.venv/bin/python3 scratch/hw20k/xcompat_validate.py` — all grid cells PASS
   (gates Step 2).
2. `.venv/bin/python3 scratch/hw20k/adaptive.py selftest` — new offline test.
3. Existing nets stay green: `sounder.py selftest`, `mfsk.py`,
   `freqresp.py selftest`, `env_sweep.py selftest`, `clib.py` loopback.
4. OTA (Step 4) when the bench is available: per-cell goodput ≥ previous
   single-mic numbers; shadowed cell shows `mrc_rescued_blocks > 0` with nonzero
   goodput.

## Sequencing note
Steps 1-3 + 5 are fully autonomous (no hardware). Step 4 needs the Pixel 7a
connected and physically placed — check `adb devices` first and pause for the
bench if it's not attached.

## Downstream: cyrinx.org (Track C, after this)

User direction (2026-06-12): publish on **cyrinx.org** (registered, Cloudflare
Pages, empty) — static site, likely **Astro**, built with the frontend-design
skill. Three content pillars: (1) **results** — the high-bitrate acoustic
transport numbers this A1 table feeds; (2) **library API** — how to integrate
Cyrinx into your own project; (3) **meta-insight** — different LLM agents
independently attempting a state-of-the-art task (acoustic transport) as a
methodology, so findings can't be in any model's test set yet the result is
independently verifiable. Not in this PR; A1's JSONL/EXPERIMENTS output should be
clean enough to cite from the site later.
