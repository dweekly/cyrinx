#!/usr/bin/env python3
"""Make the batch replay's capture of a registered scrambled profile with the Python modem.

The replay (scripts/check-batch-replay.py) needs one frame of a registered scrambled profile to
hand to every binding, looked up by its registered ID. This writes that frame from the Python
reference modem, so the bindings decode audio that the C core did not make. The payload is
sparse, eight data bytes at the head of each otherwise-zero block, which the unscrambled parent
profile cannot carry (docs/NEGATIVE_FINDINGS.md entry 26).

The capture is the clean frame between two runs of silence. No channel is applied: profile 8's
cyclic prefix is shorter than the echoes of the golden-vector generator's channel.

Usage:
  scrambled_replay_capture.py emit     # write the capture and print what the replay list needs
  scrambled_replay_capture.py verify   # regenerate and compare with the committed capture
"""
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modem as M

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
REGISTRY = os.path.join(REPO, "Tests", "Fixtures", "profiles", "cyrinx_profiles_v2.json")
PROFILE_ID = 8
CAPTURE = os.path.join(REPO, "Tests", "Fixtures", "conformance", f"registered-{PROFILE_ID}-sparse.f32")
SEED = 0x8C4A
PRE_PAD, POST_PAD = 1200, 800
BITS = {0: 1, 1: 2, 2: 4, 3: 6, 4: 8}
RATES = {0: "1/2", 1: "2/3", 2: "3/4", 3: "5/6"}


def build():
    with open(REGISTRY) as handle:
        row = next(p for p in json.load(handle)["profiles"] if p["id"] == PROFILE_ID)
    assert row["payloadScrambler"] == M.SCRAMBLER_FRAMED_BITS_V1, "the profile is not scrambled"
    band = M.Config(f_lo=row["lowFrequencyHz"], f_hi=row["highFrequencyHz"], pilot_every=row["pilotEvery"],
                    nfft=row["fftSize"], cp=row["cyclicPrefix"], sr=row["sampleRate"])
    cfg = M.Config(f_lo=row["lowFrequencyHz"], f_hi=row["highFrequencyHz"], pilot_every=row["pilotEvery"],
                   bits_per_bin={b: BITS[row["modulation"]] for b in band.data_idx},
                   rate=RATES[row["codeRate"]], n_sym=row["symbolCount"], amp=row["amplitude"],
                   clip_sigma=row["clipSigma"], nfft=row["fftSize"], cp=row["cyclicPrefix"],
                   sr=row["sampleRate"], chirp_f0=row["chirpF0"], chirp_f1=row["chirpF1"],
                   payload_scrambler=row["payloadScrambler"])
    assert cfg.n_blocks == row["blockCount"], (cfg.n_blocks, row["blockCount"])
    rng = M.DetRng(SEED)
    payload = b"".join(rng.bytes(8) + bytes(M.CRC_BLOCK - 8) for _ in range(cfg.n_blocks))
    wave = M.modulate_frame(cfg, payload)
    capture = np.concatenate([np.zeros(PRE_PAD), wave.astype(float), np.zeros(POST_PAD)]).astype("<f4")
    return row, cfg, payload, capture


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in ("emit", "verify"):
        print(__doc__)
        return 2
    row, cfg, payload, capture = build()
    digest = hashlib.sha256(capture.tobytes()).hexdigest()
    if command == "verify":
        with open(CAPTURE, "rb") as handle:
            committed = hashlib.sha256(handle.read()).hexdigest()
        if committed != digest:
            print(f"FAILED: regenerated {digest[:12]} differs from the committed {committed[:12]}")
            return 1
        print("the committed capture is what the Python modem makes")
        return 0
    # The Python receiver must recover its own frame before the frame is offered as a fixture.
    result = M.demodulate_frame(cfg, capture.astype(float))
    assert result["blocks_ok"] == cfg.n_blocks and result["payload"] == payload, \
        f"the Python receiver recovered {result['blocks_ok']} of {cfg.n_blocks} blocks"
    with open(CAPTURE, "wb") as handle:
        handle.write(capture.tobytes())
    print(json.dumps({
        "path": os.path.relpath(CAPTURE, REPO), "registered_profile": PROFILE_ID,
        "identity": row["identitySha256"], "frame_samples": cfg.frame_samples, "blocks": cfg.n_blocks,
        "samples": len(capture), "pcm_sha256": digest,
        "valid_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
