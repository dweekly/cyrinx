#!/usr/bin/env python3
"""Non-coherent multitone-FSK floor modem -- the library's robustness fallback
for reverberant topologies where coherent CP-OFDM is infeasible (delay spread
>> cyclic prefix, deep frequency nulls, phase incoherence).

Why this survives where OFDM dies:
  - ENERGY detection, not phase. Each symbol selects, per block, one of 16 tones;
    the receiver integrates per-tone energy (a DFT at the tone frequencies) and
    picks the strongest. Phase incoherence is irrelevant; a deep frequency null
    just makes one tone unavailable, not the whole symbol.
  - SYMBOL DURATION > DELAY SPREAD. With T_sym ~ 3x the spread and the detector
    integrating only the late part of each symbol, the previous symbol's
    reverberant tail has decayed before we measure -- so there is no ISI to
    equalize. This is the property a cyclic prefix cannot buy past its cap.

Frame: chirp preamble + guard + N symbols. Each symbol carries N_BLOCKS nibbles
(4 bits) via 16-FSK per block; block b value v -> tone index v*N_BLOCKS+b, so each
block's 16 candidate tones are interleaved across the band (a local null kills at
most one candidate per block). Payload is framed as [len:2][payload][crc32:4],
nibble-packed, so goodput is CRC-verified exactly like the OFDM path.

FEC (A3): Reed-Solomon RS(15,11) over GF(16) (rs16.py) replaced the original
3x repetition + majority vote -- one RS code symbol = one nibble = one tone
decision, so a wrong pick costs exactly one code symbol. Codewords are
block-interleaved position-major across the OTA symbols (one corrupted symbol
costs each codeword at most ceil(N_BLOCKS/n_cw) nibbles of its budget), and
the energy detector flags low-confidence blocks (best/second-best energy
ratio < ERASE_CONF) as ERASURES, which RS corrects at twice the rate of hard
errors -- confidence the majority vote threw away. Rate 11/15 vs 1/3 gives
~2x the net bitrate at the same symbol duration.

This is the implementation the adaptive sounder already routes to (sounder.py
NONCOHERENT_FLOOR); ggwave was the stand-in reference (data/desk_noncoherent.json,
~267 bps). Targets the same order of magnitude, trading rate for the ability to
link at all -- graceful degradation, never zero.
"""
import os
import sys
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rs16

SR = 48000
F_LO = 2000.0
F_HI = 15000.0
N_BLOCKS = 8              # nibbles per symbol -> 4 bytes/symbol of raw capacity
TONES_PER_BLOCK = 16     # 16-FSK -> 4 bits/block
N_TONES = N_BLOCKS * TONES_PER_BLOCK
T_SYM = 0.120            # symbol duration (s); must exceed the delay spread
GUARD_SKIP = 0.045       # ignore this much at each symbol's start (prev-sym tail)
RS_N, RS_K, RS_NSYM = 15, 11, 4   # FEC: RS(15,11) over GF(16), errors+erasures
ERASE_CONF = 1.3         # best/second tone-energy ratio below this -> erasure
AMP = 0.6
CHIRP_F0, CHIRP_F1, CHIRP_DUR = 2000.0, 15000.0, 0.12
GAP = 0.05               # silence between preamble and symbols


def _tone_freqs():
    return F_LO + (F_HI - F_LO) * np.arange(N_TONES) / (N_TONES - 1)


def _chirp(f0, f1, dur, sr=SR, amp=AMP):
    t = np.arange(int(dur * sr)) / sr
    w = amp * np.sin(2 * np.pi * (f0 * t + 0.5 * (f1 - f0) * t * t / dur))
    r = int(0.008 * sr)
    env = np.ones(len(w))
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    return (w * env).astype(np.float64)


def _ramp(sig, sr=SR):
    r = int(0.006 * sr)
    env = np.ones(len(sig))
    env[:r] = 0.5 - 0.5 * np.cos(np.pi * np.arange(r) / r)
    env[-r:] = env[:r][::-1]
    return sig * env


def _frame_nibbles(payload):
    body = bytes([len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload
    body += zlib.crc32(body).to_bytes(4, "little")
    nibs = []
    for byte in body:
        nibs.append(byte & 0xF)
        nibs.append(byte >> 4)
    return nibs


def _stream_geometry(body_len):
    """(n_codewords, base nibbles, stream nibbles, OTA symbols) for a body."""
    base_n = body_len * 2
    n_cw = -(-base_n // RS_K)
    total = n_cw * RS_N
    n_syms = -(-total // N_BLOCKS)
    return n_cw, base_n, total, n_syms


def _rs_stream(nibs):
    """RS-encode nibbles into the interleaved TX stream. Codewords are emitted
    position-major (stream[p*n_cw + c] = codeword c, position p) so consecutive
    OTA blocks come from different codewords; the zero-padded tail of the last
    codeword is transmitted (a shortened-code optimization is possible but not
    worth the complexity at these frame sizes)."""
    n_cw = -(-len(nibs) // RS_K)
    cws = []
    for c in range(n_cw):
        chunk = nibs[c * RS_K:(c + 1) * RS_K]
        cws.append(rs16.encode(chunk + [0] * (RS_K - len(chunk)), RS_NSYM))
    stream = [cws[c][p] for p in range(RS_N) for c in range(n_cw)]
    while len(stream) % N_BLOCKS:
        stream.append(0)
    return stream


def modulate(payload, sr=SR):
    freqs = _tone_freqs()
    nibs = _rs_stream(_frame_nibbles(payload))
    n_syms = len(nibs) // N_BLOCKS
    slen = int(T_SYM * sr)
    t = np.arange(slen) / sr
    parts = [_chirp(CHIRP_F0, CHIRP_F1, CHIRP_DUR, sr), np.zeros(int(GAP * sr))]
    for s in range(n_syms):
        sig = np.zeros(slen)
        for b in range(N_BLOCKS):
            v = nibs[s * N_BLOCKS + b]
            sig += np.sin(2 * np.pi * freqs[v * N_BLOCKS + b] * t)
        sig = _ramp(sig / (np.abs(sig).max() + 1e-12) * AMP, sr)
        parts.append(sig)
    parts.append(np.zeros(int(0.1 * sr)))
    return np.concatenate(parts).astype(np.float32)


def _find_chirp(rx, sr=SR):
    ref = _chirp(CHIRP_F0, CHIRP_F1, CHIRP_DUR, sr, amp=1.0)
    # FFT-based matched filter (fast cross-correlation)
    n = len(rx) + len(ref)
    R = np.fft.rfft(rx, n)
    H = np.fft.rfft(ref[::-1], n)
    corr = np.fft.irfft(R * H, n)
    pk = int(np.argmax(np.abs(corr[:len(rx)])))
    return pk - len(ref) + 1


def _tone_energy_matrix(rx, sr, n_syms):
    """Per-symbol tone-energy matrix E[s, tone] from a mono capture, using the
    capture's own chirp sync (each mic must self-align: reflections can pull
    the chirp locks on the two mics apart by several ms)."""
    freqs = _tone_freqs()
    start = _find_chirp(rx, sr)
    base = start + len(_chirp(CHIRP_F0, CHIRP_F1, CHIRP_DUR, sr)) + int(GAP * sr)
    slen = int(T_SYM * sr)
    skip = int(GUARD_SKIP * sr)
    wlen = slen - skip
    # precompute the DFT basis at the tone frequencies for the detection window
    basis = np.exp(-2j * np.pi * np.outer(freqs, np.arange(wlen) / sr))
    E = np.empty((n_syms, len(freqs)))
    for s in range(n_syms):
        a = base + s * slen + skip
        win = rx[a:a + wlen]
        if len(win) < wlen:
            win = np.pad(win, (0, wlen - len(win)))
        E[s] = np.abs(basis @ win) ** 2       # energy per tone
    return E


def _decide(E):
    """Hard tone decisions + confidences from an energy matrix."""
    nibs, confs = [], []
    for s in range(E.shape[0]):
        for b in range(N_BLOCKS):
            cands = E[s, b::N_BLOCKS][:TONES_PER_BLOCK]
            order = np.argsort(cands)
            nibs.append(int(order[-1]))
            # detector confidence: best vs runner-up energy; near 1 = a guess
            confs.append(float(cands[order[-1]] / (cands[order[-2]] + 1e-30)))
    return nibs, confs


def demodulate(rx, n_payload, sr=SR):
    """Decode a frame of known payload length. Returns (payload_bytes, crc_ok).

    Accepts mono or stereo (N, n_mic). For stereo, decode-based mic selection
    first: try each mic and return the first that CRC-verifies. RMS-loudness is
    a poor selector -- the louder mic is often the worse one in a reverberant
    field (measured: a spot where the louder mic failed but the quieter one
    decoded 0 byte errors). If NO single mic verifies, escalate to non-coherent
    combining: sum the mics' noise-normalized tone-energy matrices and decode
    the combined field (the MFSK analog of the coherent path's two-mic MRC --
    tone-decision errors are position-independent across mics, measured at
    edge_below_laptop 2026-07-07: a codeword 3-err on mic0 but 1-err on mic1)."""
    rx = np.asarray(rx, dtype=np.float64)
    body_len = 2 + n_payload + 4
    n_cw, base_n, total, n_syms = _stream_geometry(body_len)
    if rx.ndim > 1:
        last = (b"", False)
        for c in range(rx.shape[1]):
            dec, crc = demodulate(rx[:, c], n_payload, sr)
            if crc:
                return dec, crc
            last = (dec, crc)
        # combining escalation: scale each mic's energy matrix to unit total
        # frame energy, then sum -- equal-energy combining, so a mic with more
        # absolute gain (or a hotter reverb field) cannot swamp the other.
        # Chosen empirically over median(noise-floor) normalization: on 4
        # measured edge_below_laptop captures (2026-07-07) total-norm gave SER
        # 5.4/2.7/4.5/3.6% vs best-single-mic 6.2/6.2/4.5/6.2% (never worse),
        # while median-norm was sometimes worse than the best mic alone.
        Es = [_tone_energy_matrix(rx[:, c], sr, n_syms) for c in range(rx.shape[1])]
        E = sum(e / (e.sum() + 1e-30) for e in Es)
        nibs, confs = _decide(E)
        return _decode_stream(nibs, confs, n_cw, base_n, body_len, n_payload)
    nibs, confs = _decide(_tone_energy_matrix(rx, sr, n_syms))
    return _decode_stream(nibs, confs, n_cw, base_n, body_len, n_payload)


def _decode_stream(nibs, confs, n_cw, base_n, body_len, n_payload):
    # deinterleave into codewords; low-confidence blocks become RS erasures
    # (capped at the RS_NSYM budget, keeping the least confident)
    voted = []
    for c in range(n_cw):
        cw = [nibs[p * n_cw + c] for p in range(RS_N)]
        cf = [confs[p * n_cw + c] for p in range(RS_N)]
        er = sorted((p for p in range(RS_N) if cf[p] < ERASE_CONF),
                    key=lambda p: cf[p])[:RS_NSYM]
        # Errors-only decode FIRST: over the air, reverberant multipath yields
        # low best/runner-up energy ratios on many CORRECT decisions, so
        # confidence-flagged "erasures" are often not errors; each false
        # erasure burns budget (2e + s <= nsym) and overloads codewords that
        # plain error correction handles (measured at edge_below_laptop
        # 2026-07-07: raw SER 6.2%, <=2 err/cw, yet erasure-first failed the
        # frame). Erasure-assisted decode remains the fallback: at the true
        # low-SNR edge errors-only overloads -> None, and the erasure path
        # provides the measured rep3-beating gain (see _selftest).
        dec = rs16.decode(cw, RS_NSYM)
        if dec is None:
            dec = rs16.decode(cw, RS_NSYM, erase_pos=er)
        # on RS overload fall back to the raw hard decisions; CRC judges
        voted.extend((dec if dec is not None else cw)[:RS_K])
    # reassemble bytes
    out = bytearray()
    for i in range(0, base_n - 1, 2):
        out.append((voted[i] & 0xF) | (voted[i + 1] << 4))
    body = bytes(out[:body_len])
    crc_ok = (len(body) == body_len and
              zlib.crc32(body[:-4]) == int.from_bytes(body[-4:], "little"))
    payload = body[2:2 + n_payload]
    return payload, crc_ok


def bitrate(n_payload):
    """Net payload bitrate (bps) for a frame of n_payload bytes (incl. RS FEC)."""
    _, _, _, n_syms = _stream_geometry(2 + n_payload + 4)
    airtime = CHIRP_DUR + GAP + n_syms * T_SYM
    return n_payload * 8 / airtime


# ------------------------------ offline self-test ------------------------------

def _reverb_channel(x, sr=SR, spread_ms=40.0, seed=3):
    """Synthetic reverberant channel: direct tap + exponentially decaying dense
    tail out to spread_ms (the regime that kills CP-OFDM at any MCS)."""
    rng = np.random.default_rng(seed)
    L = int(spread_ms / 1000 * sr)
    tau = L / 3.0
    h = rng.normal(0, 1, L) * np.exp(-np.arange(L) / tau)
    h[0] = 1.0                                # strong direct tap
    y = np.convolve(x.astype(np.float64), h, mode="full")[:len(x)]
    return y


def _notch(x, sr, lo, hi):
    """Brick-wall kill of one frequency band -- models a deep transducer or
    room null. RS erasure flagging (not the majority vote) is what survives
    this: the correct tone simply has no energy, and the detector knows it."""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    X[(f >= lo) & (f <= hi)] = 0.0
    return np.fft.irfft(X, len(x))


def _selftest():
    payload = bytes((i * 53 + 7) & 0xFF for i in range(16))
    wave = modulate(payload)
    # rate vs the pre-A3 3x-repetition scheme, same symbol duration
    rep_syms = (-(-((2 + len(payload) + 4) * 2) // N_BLOCKS) * N_BLOCKS) * 3 // N_BLOCKS
    rep_bps = len(payload) * 8 / (CHIRP_DUR + GAP + rep_syms * T_SYM)
    print(f"  frame: {len(payload)} B payload, {len(wave)/SR*1000:.0f} ms airtime, "
          f"net {bitrate(len(payload)):.0f} bps "
          f"(3x-repetition was {rep_bps:.0f} bps -> "
          f"x{bitrate(len(payload))/rep_bps:.2f})")
    rng = np.random.default_rng(1)
    ok = True
    # the 0 dB case is BELOW the old 3x-repetition scheme's failure edge
    # (measured: rep3 16/20 at 0 dB, RS 20/20; at -3 dB rep3 5/20, RS 18/20)
    for spread, snr_db in [(0.0, 60), (40.0, 20), (40.0, 12), (50.0, 15),
                           (40.0, 0)]:
        rx = _reverb_channel(wave, spread_ms=spread) if spread > 0 else wave.astype(float)
        sig_rms = np.sqrt((rx ** 2).mean())
        rx = rx + rng.normal(0, sig_rms / (10 ** (snr_db / 20)), len(rx))
        rx = np.concatenate([np.zeros(2000), rx, np.zeros(2000)])
        dec, crc = demodulate(rx, len(payload))
        good = crc and dec == payload
        ok &= good
        print(f"    spread={spread:4.0f}ms SNR={snr_db}dB -> crc_ok={crc} "
              f"payload_match={dec == payload}  {'OK' if good else 'FAIL'}")
    # erasure case: a dead band on top of reverb -- correct tones inside the
    # notch have no energy, so those blocks arrive as low-confidence erasures
    rx = _notch(_reverb_channel(wave, spread_ms=40.0), SR, 5000.0, 6000.0)
    sig_rms = np.sqrt((rx ** 2).mean())
    rx = rx + rng.normal(0, sig_rms / (10 ** (15 / 20)), len(rx))
    rx = np.concatenate([np.zeros(2000), rx, np.zeros(2000)])
    dec, crc = demodulate(rx, len(payload))
    good = crc and dec == payload
    ok &= good
    print(f"    spread=  40ms SNR=15dB + 5-6 kHz band killed -> crc_ok={crc} "
          f"payload_match={dec == payload}  {'OK' if good else 'FAIL'}")
    # combining escalation: complementary dead bands + heavy noise per mic so
    # NEITHER mic decodes alone, but the summed (unit-total-energy) field does
    # -- the stereo path measured OTA at edge_below_laptop 2026-07-07
    mics = []
    for lo, hi, seed in [(4000.0, 7500.0, 7), (9000.0, 12500.0, 8)]:
        r = _notch(_reverb_channel(wave, spread_ms=40.0, seed=seed), SR, lo, hi)
        sig_rms = np.sqrt((r ** 2).mean())
        r = r + np.random.default_rng(seed).normal(
            0, sig_rms / (10 ** (6 / 20)), len(r))
        mics.append(np.concatenate([np.zeros(2000), r, np.zeros(2000)]))
    n = min(len(m) for m in mics)
    st = np.stack([m[:n] for m in mics], axis=1)
    alone = [demodulate(st[:, c], len(payload)) for c in range(2)]
    solo_fail = all(not (crc and dec == payload) for dec, crc in alone)
    dec, crc = demodulate(st, len(payload))
    good = solo_fail and crc and dec == payload
    ok &= good
    print(f"    stereo combining: mic0 alone={alone[0][1]} mic1 alone={alone[1][1]} "
          f"combined crc_ok={crc} payload_match={dec == payload}  "
          f"{'OK' if good else 'FAIL'}")
    print("  SELFTEST", "PASS" if ok else "FAIL",
          "(coherent CP-OFDM fails at every one of the >32ms-spread cases)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
