#!/usr/bin/env python3
"""Python binding for the research clock-offset decoder (clock_offset.c).

The research dylib contains the whole unmodified C codec plus three extra
functions: estimate a frame's clock offset from its training symbols, resample
a capture, and decode with both applied. Build it with the command in
scratch/garage/README.md, or point CYRINX_CLOCK_CODEC_PATH at a built copy.
"""

import ctypes
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "hw20k"))

import clib  # noqa: E402

PATH_ENV = "CYRINX_CLOCK_CODEC_PATH"
DEFAULT_PATH = os.path.join(_HERE, "libcyrinxclock.dylib")
MAX_ABS_PPM = 150.0
"""Search range. Consumer audio clocks are specified to about +/-50 ppm each, so
two devices can differ by about 100; the range leaves margin beyond that."""

PASSES = 2

_F = ctypes.POINTER(ctypes.c_float)
_LIB = None


def library_path():
    return os.environ.get(PATH_ENV, DEFAULT_PATH)


def lib():
    global _LIB
    if _LIB is None:
        library = ctypes.CDLL(library_path())
        library.cyrinx_research_resample.argtypes = [_F, ctypes.c_size_t, ctypes.c_double, _F,
                                                     ctypes.c_size_t]
        library.cyrinx_research_resample.restype = ctypes.c_long
        library.cyrinx_research_estimate_clock_offset.argtypes = [
            ctypes.POINTER(clib.Cfg), _F, ctypes.c_size_t, ctypes.c_double,
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)]
        library.cyrinx_research_estimate_clock_offset.restype = ctypes.c_int
        library.cyrinx_research_demodulate_clock_corrected.argtypes = [
            ctypes.POINTER(clib.Cfg), _F, ctypes.c_size_t, ctypes.c_double, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t, ctypes.POINTER(ctypes.c_double)]
        library.cyrinx_research_demodulate_clock_corrected.restype = ctypes.c_long
        library.cyrinx_research_set_pilot_fit.argtypes = [ctypes.c_int]
        library.cyrinx_research_set_pilot_fit.restype = ctypes.c_int
        _LIB = library
    return _LIB


FIT_SHIPPED, FIT_COHERENT = 0, 1


def set_pilot_fit(mode):
    """Choose the pilot timing fit for every decode through the research dylib.

    FIT_SHIPPED (the default) decodes exactly as the codec does. FIT_COHERENT
    is the research fit described in clock_offset.c. This only affects decodes
    when clib is loaded from the same research dylib. Returns the previous mode.
    """
    previous = lib().cyrinx_research_set_pilot_fit(mode)
    if previous < 0:
        raise ValueError(f"unknown pilot fit mode {mode}")
    return previous


def resample(rx, ppm):
    """rx read at times scaled by (1 + ppm * 1e-6); same length as rx."""
    src = np.ascontiguousarray(rx, dtype=np.float32)
    out = np.zeros(len(src), dtype=np.float32)
    if lib().cyrinx_research_resample(src.ctypes.data_as(_F), len(src), ppm,
                                      out.ctypes.data_as(_F), len(out)) < 0:
        raise ValueError("resample rejected its input")
    return out


def estimate(cfg, rx, max_abs_ppm=MAX_ABS_PPM):
    """(ppm, coherence) for the strongest frame in rx, or None if none fits."""
    src = np.ascontiguousarray(rx, dtype=np.float32)
    ppm = ctypes.c_double(0.0)
    coherence = ctypes.c_double(0.0)
    if lib().cyrinx_research_estimate_clock_offset(
            ctypes.byref(cfg), src.ctypes.data_as(_F), len(src), max_abs_ppm,
            ctypes.byref(ppm), ctypes.byref(coherence)) != 0:
        return None
    return ppm.value, coherence.value


def decode(cfg, rx, max_abs_ppm=MAX_ABS_PPM, passes=PASSES):
    """clib.decode's result with a "ppm" key: the clock correction that was applied."""
    geom = clib.geometry(cfg)
    src = np.ascontiguousarray(rx, dtype=np.float32)
    out = (ctypes.c_uint8 * geom.payload_bytes)()
    mask = (ctypes.c_uint8 * geom.n_blocks)()
    ok, total = ctypes.c_int(0), ctypes.c_int(0)
    evm, ppm = ctypes.c_double(0.0), ctypes.c_double(0.0)
    n = lib().cyrinx_research_demodulate_clock_corrected(
        ctypes.byref(cfg), src.ctypes.data_as(_F), len(src), max_abs_ppm, passes, out,
        geom.payload_bytes, ctypes.byref(ok), ctypes.byref(total), ctypes.byref(evm), mask,
        geom.n_blocks, ctypes.byref(ppm))
    if n < 0:
        return None
    return {"payload": bytes(out), "blocks_ok": ok.value, "blocks_total": total.value,
            "block_valid": [bool(mask[i]) for i in range(geom.n_blocks)], "evm": evm.value,
            "ppm": ppm.value}
