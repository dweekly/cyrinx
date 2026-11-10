"""BPSK (1 bit per bin) in the Python reference modem, and its parity with C.

NF-12 used to record 1-bit bins as broken. The cause was `qam_map` doing
`1 - 2 * bits` on a uint8 array: under NumPy 2 that stays uint8 and wraps, so a
1 bit mapped to +255 instead of -1. These tests pin the mapping and the round
trips, so the constellation cannot drift again without failing here.
"""

import numpy as np
import pytest

import modem as M


def _payload(n, seed=1):
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


def _loopback(wave, lead=1000, tail=1000):
    return np.concatenate([np.zeros(lead), np.asarray(wave, float), np.zeros(tail)])


def test_bpsk_maps_uint8_bits_to_plus_and_minus_one():
    bits = np.array([[0], [1], [1], [0]], dtype=np.uint8)
    assert M.qam_map(bits, 1).tolist() == [1 + 0j, -1 + 0j, -1 + 0j, 1 + 0j]


@pytest.mark.parametrize(
    "name,bits_for",
    [
        ("uniform BPSK", lambda i: 1),
        ("BPSK mixed with QPSK", lambda i: 1 + i % 2),
        ("BPSK mixed with 16-QAM", lambda i: 1 if i % 3 else 4),
    ],
)
def test_python_loopback_decodes_maps_with_one_bit_bins(name, bits_for):
    bins = M.Config().data_idx
    cfg = M.Config(rate="1/2", n_sym=24, bits_per_bin={b: bits_for(i) for i, b in enumerate(bins)})
    payload = _payload(cfg.payload_bytes)
    res = M.demodulate_frame(cfg, _loopback(M.modulate_frame(cfg, payload)))
    assert res["blocks_ok"] == res["blocks_total"] > 0, name
    assert res["payload"] == payload, name
    assert res["evm_rms"] < 0.05, f"{name}: EVM {res['evm_rms']}"


def test_c_and_python_agree_on_uniform_bpsk(codec):
    cfg = codec.make_cfg(bits_per_bin=1, rate="1/2", n_sym=24)
    mcfg = codec.modem_cfg_from_clib(cfg)
    payload = _payload(codec.geometry(cfg).payload_bytes)

    c_wave = np.asarray(codec.encode(cfg, payload), float)
    py_wave = np.asarray(M.modulate_frame(mcfg, payload), float)
    assert c_wave.shape == py_wave.shape
    assert np.max(np.abs(c_wave - py_wave)) < 1e-4, "C and Python BPSK waveforms differ"

    from_c = M.demodulate_frame(mcfg, _loopback(c_wave))
    assert from_c["payload"] == payload, "Python could not decode the C BPSK frame"

    from_py = codec.decode(cfg, _loopback(py_wave).astype(np.float32))
    assert from_py and from_py["payload"] == payload, "C could not decode the Python BPSK frame"
    assert all(from_py["block_valid"])
