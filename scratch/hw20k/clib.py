#!/usr/bin/env python3
"""ctypes binding to the *library* C bulk-PHY codec (libcyrinxbulk.dylib, built
from Sources/CCyrinx/cyrinx_bulk.c + cyrinx_fft.c + kissfft). Lets the OTA
harness drive the SAME C code that ships in the library, so the measured
over-the-air goodput is library-native (docs/PUBLICATION.md PR 1.10), not the
Python reference modem.

Rebuild the dylib:
  clang -O2 -dynamiclib -Dkiss_fft_scalar=double \
    -ISources/CCyrinx/include -ISources/CCyrinx/kissfft \
    Sources/CCyrinx/cyrinx_bulk.c Sources/CCyrinx/cyrinx_fft.c \
    Sources/CCyrinx/kissfft/kiss_fft.c Sources/CCyrinx/kissfft/kiss_fftr.c \
    -o scratch/hw20k/libcyrinxbulk.dylib -lm
"""
import ctypes
import os

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = ctypes.CDLL(os.path.join(_HERE, "libcyrinxbulk.dylib"))


class Cfg(ctypes.Structure):
    _fields_ = [
        ("f_lo", ctypes.c_double), ("f_hi", ctypes.c_double),
        ("pilot_every", ctypes.c_int), ("bits_per_bin", ctypes.c_int),
        ("rate", ctypes.c_char_p), ("n_sym", ctypes.c_int),
        ("nfft", ctypes.c_int), ("cp", ctypes.c_int), ("sr", ctypes.c_int),
        ("amp", ctypes.c_double), ("clip_sigma", ctypes.c_double),
        ("chirp_f0", ctypes.c_double), ("chirp_f1", ctypes.c_double),
    ]


class Geo(ctypes.Structure):
    _fields_ = [(n, ctypes.c_int) for n in (
        "bin_lo", "bin_hi", "n_used", "n_pilots", "n_data_bins", "bits_per_sym",
        "cap", "info_bits", "n_blocks", "payload_bytes", "frame_samples")]


_LIB.cyrinx_bulk_compute_geometry.argtypes = [ctypes.POINTER(Cfg), ctypes.POINTER(Geo)]
_LIB.cyrinx_bulk_compute_geometry.restype = ctypes.c_int
_LIB.cyrinx_bulk_modulate.argtypes = [
    ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.POINTER(ctypes.c_double)]
_LIB.cyrinx_bulk_modulate.restype = ctypes.c_long
_LIB.cyrinx_bulk_demodulate.argtypes = [
    ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_double)]
_LIB.cyrinx_bulk_demodulate.restype = ctypes.c_long


def make_cfg(bits_per_bin=4, rate="3/4", n_sym=64, f_lo=1100.0, f_hi=23000.0,
             nfft=2048, cp=768, sr=48000, amp=0.5, clip_sigma=3.3):
    return Cfg(f_lo, f_hi, 8, bits_per_bin, rate.encode(), n_sym, nfft, cp, sr,
               amp, clip_sigma, 2000.0, 16000.0)


def geometry(cfg):
    g = Geo()
    if _LIB.cyrinx_bulk_compute_geometry(ctypes.byref(cfg), ctypes.byref(g)) != 0:
        raise ValueError("invalid config")
    return g


def encode(cfg, payload):
    g = geometry(cfg)
    assert len(payload) == g.payload_bytes, (len(payload), g.payload_bytes)
    wave = (ctypes.c_float * g.frame_samples)()
    pb = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
    n = _LIB.cyrinx_bulk_modulate(ctypes.byref(cfg), pb, len(payload), wave,
                                  g.frame_samples, None)
    if n < 0:
        raise RuntimeError("modulate failed")
    return np.ctypeslib.as_array(wave)[:n].copy()


def decode(cfg, rx):
    g = geometry(cfg)
    rxf = np.ascontiguousarray(rx, dtype=np.float32)
    out = (ctypes.c_uint8 * g.payload_bytes)()
    ok = ctypes.c_int(0)
    total = ctypes.c_int(0)
    evm = ctypes.c_double(0.0)
    rxp = rxf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    n = _LIB.cyrinx_bulk_demodulate(ctypes.byref(cfg), rxp, len(rxf), out,
                                    g.payload_bytes, ctypes.byref(ok),
                                    ctypes.byref(total), ctypes.byref(evm))
    if n < 0:
        return None
    return {"payload": bytes(out), "blocks_ok": ok.value,
            "blocks_total": total.value, "evm": evm.value}


if __name__ == "__main__":
    # digital loopback sanity check through the library C code
    for bpb, rate in [(2, "1/2"), (4, "3/4"), (6, "3/4")]:
        cfg = make_cfg(bits_per_bin=bpb, rate=rate, n_sym=8)
        g = geometry(cfg)
        payload = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
        wave = encode(cfg, payload)
        rx = np.concatenate([np.zeros(3000, np.float32), wave, np.zeros(2000, np.float32)])
        r = decode(cfg, rx)
        ok = r and r["payload"] == payload and r["blocks_ok"] == r["blocks_total"]
        print(f"  bpb={bpb} rate={rate}: blocks {r['blocks_ok']}/{r['blocks_total']} "
              f"payload_match={r['payload']==payload} -> {'OK' if ok else 'FAIL'}")
