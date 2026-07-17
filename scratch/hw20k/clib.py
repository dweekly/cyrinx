#!/usr/bin/env python3
"""ctypes binding to the *library* C bulk-PHY codec (libcyrinxbulk.dylib, built
from Sources/CCyrinx/cyrinx_bulk.c + cyrinx_fft.c + kissfft). Lets the OTA
harness drive the SAME C code that ships in the library, so the measured
over-the-air goodput is library-native (docs/PUBLICATION.md PR 1.10), not the
Python reference modem.

Rebuild the dylib:
  clang -std=c11 -O2 -dynamiclib -Dkiss_fft_scalar=double \
    -ISources/CCyrinx/include -ISources/CCyrinx/kissfft \
    Sources/CCyrinx/cyrinx_bulk.c Sources/CCyrinx/cyrinx_fft.c \
    Sources/CCyrinx/kissfft/kiss_fft.c Sources/CCyrinx/kissfft/kiss_fftr.c \
    -o scratch/hw20k/libcyrinxbulk.dylib -lm
"""
import ctypes
import math
import os
from pathlib import Path
import threading

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
CODEC_PATH_ENV = "CYRINX_BULK_CODEC_PATH"
AUTO_V1_DIAGNOSTICS_ABI_VERSION = 1
AUTO_V1_POLICY_VERSION = 1
AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO = 0.95


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


class DiversityDiagnostics(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("abi_version", ctypes.c_uint32),
        ("policy_version", ctypes.c_int),
        ("selected_receiver", ctypes.c_int),
        ("scores_valid", ctypes.c_int),
        ("selection_reason", ctypes.c_int),
        ("validation_observations", ctypes.c_int),
        ("primary_holdout_pilot_rms", ctypes.c_double),
        ("mrc_holdout_pilot_rms", ctypes.c_double),
        ("observed_mrc_to_primary_pilot_rms_ratio", ctypes.c_double),
        ("maximum_mrc_to_primary_pilot_rms_ratio", ctypes.c_double),
    ]


def _configure_library(library):
    library.cyrinx_bulk_compute_geometry.argtypes = [ctypes.POINTER(Cfg), ctypes.POINTER(Geo)]
    library.cyrinx_bulk_compute_geometry.restype = ctypes.c_int
    library.cyrinx_bulk_modulate.argtypes = [
        ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_float), ctypes.c_size_t, ctypes.POINTER(ctypes.c_double)]
    library.cyrinx_bulk_modulate.restype = ctypes.c_long
    library.cyrinx_bulk_demodulate.argtypes = [
        ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_double)]
    library.cyrinx_bulk_demodulate.restype = ctypes.c_long
    library.cyrinx_bulk_demodulate2.argtypes = [
        ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_double)]
    library.cyrinx_bulk_demodulate2.restype = ctypes.c_long

    demodulate_with_block_validity = getattr(
        library, "cyrinx_bulk_demodulate_with_block_validity", None)
    demodulate2_with_block_validity = getattr(
        library, "cyrinx_bulk_demodulate2_with_block_validity", None)
    demodulate2_auto_v1 = getattr(library, "cyrinx_bulk_demodulate2_auto_v1", None)
    demodulate2_auto_v1_with_block_validity = getattr(
        library, "cyrinx_bulk_demodulate2_auto_v1_with_block_validity", None)
    has_block_validity_api = (
        demodulate_with_block_validity is not None
        and demodulate2_with_block_validity is not None
    )
    if has_block_validity_api:
        demodulate_with_block_validity.argtypes = [
            ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
        ]
        demodulate_with_block_validity.restype = ctypes.c_long
        demodulate2_with_block_validity.argtypes = [
            ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
        ]
        demodulate2_with_block_validity.restype = ctypes.c_long
    if demodulate2_auto_v1 is not None:
        demodulate2_auto_v1.argtypes = [
            ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(DiversityDiagnostics),
        ]
        demodulate2_auto_v1.restype = ctypes.c_long
    if demodulate2_auto_v1_with_block_validity is not None:
        demodulate2_auto_v1_with_block_validity.argtypes = [
            ctypes.POINTER(Cfg), ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t, ctypes.POINTER(DiversityDiagnostics),
        ]
        demodulate2_auto_v1_with_block_validity.restype = ctypes.c_long
    return (
        demodulate_with_block_validity,
        demodulate2_with_block_validity,
        demodulate2_auto_v1,
        demodulate2_auto_v1_with_block_validity,
    )


class BulkCodec:
    """One explicitly selected bulk-PHY dynamic library.

    The physical runner continues to use the module-level default codec. Offline
    reanalysis uses this class so the caller-selected binary cannot be replaced
    by import-path or current-working-directory behavior.
    """

    def __init__(self, library_path):
        path = Path(library_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"bulk codec path is not a file: {path}")
        self.library_path = path
        self._lib = ctypes.CDLL(str(path))
        functions = _configure_library(self._lib)
        self._demodulate_with_block_validity = functions[0]
        self._demodulate2_with_block_validity = functions[1]
        self._demodulate2_auto_v1 = functions[2]
        self._demodulate2_auto_v1_with_block_validity = functions[3]
        self.has_block_validity_api = all(function is not None for function in functions[:2])
        self.has_auto_v1_api = all(function is not None for function in functions[2:])

    def geometry(self, cfg):
        g = Geo()
        if self._lib.cyrinx_bulk_compute_geometry(ctypes.byref(cfg), ctypes.byref(g)) != 0:
            raise ValueError("invalid config")
        return g

    def encode(self, cfg, payload):
        g = self.geometry(cfg)
        if len(payload) != g.payload_bytes:
            raise ValueError(f"payload has {len(payload)} bytes, expected {g.payload_bytes}")
        wave = (ctypes.c_float * g.frame_samples)()
        pb = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
        n = self._lib.cyrinx_bulk_modulate(
            ctypes.byref(cfg), pb, len(payload), wave, g.frame_samples, None)
        if n < 0:
            raise RuntimeError("modulate failed")
        return np.ctypeslib.as_array(wave)[:n].copy()

    def _require_block_validity_api(self, allow_legacy_block_counts):
        if self.has_block_validity_api or allow_legacy_block_counts:
            return
        raise RuntimeError(
            f"{self.library_path} lacks ordered block-validity symbols; rebuild it "
            "from the current Sources/CCyrinx sources, or explicitly pass "
            "allow_legacy_block_counts=True for aggregate-only diagnostics")

    def decode(self, cfg, rx, *, allow_legacy_block_counts=False):
        self._require_block_validity_api(allow_legacy_block_counts)
        g = self.geometry(cfg)
        rxf = np.ascontiguousarray(rx, dtype=np.float32)
        out = (ctypes.c_uint8 * g.payload_bytes)()
        mask = (ctypes.c_uint8 * g.n_blocks)()
        ok = ctypes.c_int(0)
        total = ctypes.c_int(0)
        evm = ctypes.c_double(0.0)
        rxp = rxf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        if self.has_block_validity_api:
            n = self._demodulate_with_block_validity(
                ctypes.byref(cfg), rxp, len(rxf), out, g.payload_bytes,
                ctypes.byref(ok), ctypes.byref(total), ctypes.byref(evm), mask,
                g.n_blocks)
            block_valid = [bool(mask[index]) for index in range(g.n_blocks)]
        else:
            n = self._lib.cyrinx_bulk_demodulate(
                ctypes.byref(cfg), rxp, len(rxf), out, g.payload_bytes,
                ctypes.byref(ok), ctypes.byref(total), ctypes.byref(evm))
            block_valid = None
        return _decode_result(n, out, ok, total, evm, block_valid)

    def decode2(self, cfg, rx, rx2, *, allow_legacy_block_counts=False):
        self._require_block_validity_api(allow_legacy_block_counts)
        g = self.geometry(cfg)
        r1 = np.ascontiguousarray(rx, dtype=np.float32)
        r2 = np.ascontiguousarray(rx2, dtype=np.float32)
        out = (ctypes.c_uint8 * g.payload_bytes)()
        mask = (ctypes.c_uint8 * g.n_blocks)()
        ok = ctypes.c_int(0)
        total = ctypes.c_int(0)
        evm = ctypes.c_double(0.0)
        if self.has_block_validity_api:
            n = self._demodulate2_with_block_validity(
                ctypes.byref(cfg),
                r1.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(r1),
                r2.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(r2),
                out, g.payload_bytes, ctypes.byref(ok), ctypes.byref(total),
                ctypes.byref(evm), mask, g.n_blocks)
            block_valid = [bool(mask[index]) for index in range(g.n_blocks)]
        else:
            n = self._lib.cyrinx_bulk_demodulate2(
                ctypes.byref(cfg),
                r1.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(r1),
                r2.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(r2),
                out, g.payload_bytes, ctypes.byref(ok), ctypes.byref(total),
                ctypes.byref(evm))
            block_valid = None
        return _decode_result(n, out, ok, total, evm, block_valid)

    def decode2_auto_v1(self, cfg, rx, rx2):
        if not self.has_auto_v1_api:
            raise RuntimeError(
                f"{self.library_path} lacks automatic-diversity policy-v1 symbols; "
                "rebuild it from the current Sources/CCyrinx sources")
        g = self.geometry(cfg)
        r1 = np.ascontiguousarray(rx, dtype=np.float32)
        if rx2 is None:
            r2 = None
        else:
            candidate = np.ascontiguousarray(rx2, dtype=np.float32)
            r2 = None if candidate.size == 0 else candidate
        out = (ctypes.c_uint8 * g.payload_bytes)()
        mask = (ctypes.c_uint8 * g.n_blocks)()
        ok = ctypes.c_int(0)
        total = ctypes.c_int(0)
        evm = ctypes.c_double(0.0)
        diagnostics = DiversityDiagnostics()
        r2_pointer = (
            None if r2 is None
            else r2.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
        r2_length = 0 if r2 is None else len(r2)
        n = self._demodulate2_auto_v1_with_block_validity(
            ctypes.byref(cfg),
            r1.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(r1),
            r2_pointer, r2_length,
            out, g.payload_bytes, ctypes.byref(ok), ctypes.byref(total),
            ctypes.byref(evm), mask, g.n_blocks, ctypes.byref(diagnostics))
        if n < 0:
            return None
        block_valid = [bool(mask[index]) for index in range(g.n_blocks)]
        return _decode_result(
            n, out, ok, total, evm, block_valid,
            diversity=_diversity_result(diagnostics))


def _diversity_result(diagnostics):
    reasons = {
        0: "not_evaluated",
        1: "mrc_improved",
        2: "primary_margin_not_met",
        3: "insufficient_pilots",
        4: "nonfinite_score",
        5: "resource_failure",
        6: "second_unavailable",
    }
    expected_size = ctypes.sizeof(DiversityDiagnostics)
    if diagnostics.struct_size != expected_size:
        raise RuntimeError(
            f"automatic-diversity diagnostics size {diagnostics.struct_size} "
            f"!= binding size {expected_size}")
    if (diagnostics.abi_version != AUTO_V1_DIAGNOSTICS_ABI_VERSION
            or diagnostics.policy_version != AUTO_V1_POLICY_VERSION):
        raise RuntimeError(
            "unsupported automatic-diversity diagnostics ABI/policy: "
            f"abi={diagnostics.abi_version}, policy={diagnostics.policy_version}")
    if diagnostics.selected_receiver not in (0, 1):
        raise RuntimeError(
            f"unknown automatic-diversity receiver: {diagnostics.selected_receiver}")
    if diagnostics.scores_valid not in (0, 1):
        raise RuntimeError(
            f"invalid automatic-diversity score-valid flag: {diagnostics.scores_valid}")
    if diagnostics.selection_reason not in reasons:
        raise RuntimeError(
            f"unknown automatic-diversity reason: {diagnostics.selection_reason}")
    maximum_ratio = diagnostics.maximum_mrc_to_primary_pilot_rms_ratio
    if (not math.isfinite(maximum_ratio)
            or maximum_ratio != AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO):
        raise RuntimeError(
            "unsupported automatic-diversity maximum MRC pilot RMS ratio: "
            f"{maximum_ratio!r} != "
            f"{AUTO_V1_MAX_MRC_TO_PRIMARY_PILOT_RMS_RATIO!r}")

    def finite_or_none(value):
        return value if math.isfinite(value) else None

    return {
        "struct_size": diagnostics.struct_size,
        "abi_version": diagnostics.abi_version,
        "policy_version": diagnostics.policy_version,
        "selected_receiver": "mrc01" if diagnostics.selected_receiver == 1 else "mic0",
        "scores_valid": bool(diagnostics.scores_valid),
        "selection_reason": reasons.get(diagnostics.selection_reason, "unknown"),
        "payload_or_crc_used_for_selection": False,
        "data_bins_used_for_selection": False,
        "validation_observations": diagnostics.validation_observations,
        "primary_holdout_pilot_rms": finite_or_none(
            diagnostics.primary_holdout_pilot_rms),
        "mrc_holdout_pilot_rms": finite_or_none(
            diagnostics.mrc_holdout_pilot_rms),
        "observed_mrc_to_primary_pilot_rms_ratio": (
            finite_or_none(diagnostics.observed_mrc_to_primary_pilot_rms_ratio)),
        "maximum_mrc_to_primary_pilot_rms_ratio": maximum_ratio,
    }


def _decode_result(n, out, ok, total, evm, block_valid, diversity=None):
    if n < 0:
        return None
    if block_valid is not None and sum(block_valid) != ok.value:
        raise RuntimeError("ordered CRC-validity mask disagrees with blocks_ok")
    result = {
        "payload": bytes(out),
        "blocks_ok": ok.value,
        "blocks_total": total.value,
        "block_valid": block_valid,
        "evm": evm.value,
    }
    if diversity is not None:
        result["automatic_diversity"] = diversity
    return result


_DEFAULT_CODEC_PATH = os.environ.get(
    CODEC_PATH_ENV, os.path.join(_HERE, "libcyrinxbulk.dylib"))
_DEFAULT_CODEC = None
_DEFAULT_CODEC_LOCK = threading.Lock()
_LIB = None
_DEMODULATE_WITH_BLOCK_VALIDITY = None
_DEMODULATE2_WITH_BLOCK_VALIDITY = None
_HAS_BLOCK_VALIDITY_API = None
_DEMODULATE2_AUTO_V1 = None
_DEMODULATE2_AUTO_V1_WITH_BLOCK_VALIDITY = None
_HAS_AUTO_V1_API = None


def _default_codec():
    """Load the module-level codec only when a module-level operation needs it."""
    global _DEFAULT_CODEC
    global _LIB
    global _DEMODULATE_WITH_BLOCK_VALIDITY
    global _DEMODULATE2_WITH_BLOCK_VALIDITY
    global _HAS_BLOCK_VALIDITY_API
    global _DEMODULATE2_AUTO_V1
    global _DEMODULATE2_AUTO_V1_WITH_BLOCK_VALIDITY
    global _HAS_AUTO_V1_API
    if _DEFAULT_CODEC is None:
        with _DEFAULT_CODEC_LOCK:
            if _DEFAULT_CODEC is None:
                codec = BulkCodec(_DEFAULT_CODEC_PATH)
                _DEFAULT_CODEC = codec
                _LIB = codec._lib
                _DEMODULATE_WITH_BLOCK_VALIDITY = codec._demodulate_with_block_validity
                _DEMODULATE2_WITH_BLOCK_VALIDITY = codec._demodulate2_with_block_validity
                _HAS_BLOCK_VALIDITY_API = codec.has_block_validity_api
                _DEMODULATE2_AUTO_V1 = codec._demodulate2_auto_v1
                _DEMODULATE2_AUTO_V1_WITH_BLOCK_VALIDITY = (
                    codec._demodulate2_auto_v1_with_block_validity)
                _HAS_AUTO_V1_API = codec.has_auto_v1_api
    return _DEFAULT_CODEC


def make_cfg(bits_per_bin=4, rate="3/4", n_sym=64, f_lo=1100.0, f_hi=23000.0,
             nfft=2048, cp=768, sr=48000, amp=0.5, clip_sigma=3.3,
             pilot_every=8):
    return Cfg(f_lo, f_hi, pilot_every, bits_per_bin, rate.encode(), n_sym, nfft, cp, sr,
               amp, clip_sigma, 2000.0, 16000.0)


def geometry(cfg):
    return _default_codec().geometry(cfg)


def encode(cfg, payload):
    return _default_codec().encode(cfg, payload)


def _require_block_validity_api(allow_legacy_block_counts):
    _default_codec()._require_block_validity_api(allow_legacy_block_counts)


def decode(cfg, rx, *, allow_legacy_block_counts=False):
    """Decode one channel and return an ordered per-payload-block CRC mask.

    Current-library decoding is strict by default. The opt-in legacy path is
    retained only for old local dylibs and returns ``block_valid=None`` because
    an aggregate count cannot identify which payload positions are valid.
    """
    return _default_codec().decode(
        cfg, rx, allow_legacy_block_counts=allow_legacy_block_counts)


def decode2(cfg, rx, rx2, *, allow_legacy_block_counts=False):
    """Two-mic library decode with per-subcarrier MRC (cyrinx_bulk_demodulate2,
    A2). rx/rx2: sample-aligned captures (two channels of one stereo capture);
    sync runs on rx. Returns the same ordered ``block_valid`` mask as decode;
    old aggregate-only dylibs require an explicit opt-in and return None."""
    return _default_codec().decode2(
        cfg, rx, rx2, allow_legacy_block_counts=allow_legacy_block_counts)


def decode2_auto_v1(cfg, rx, rx2):
    """Decode with frozen payload-independent automatic-diversity policy v1."""
    return _default_codec().decode2_auto_v1(cfg, rx, rx2)


def loaded_library_path():
    """Return the resolved binary backing the module-level physical codec."""
    return _default_codec().library_path


def modem_cfg_from_clib(cfg):
    """modem.Config equivalent of a clib Cfg (same geometry, PRNGs, framing),
    so the Python reference RX — including two-mic MRC via
    modem.demodulate_frame(rx2=...) — can decode frames produced by the library
    C codec (A1, docs/A1_AUTO_MRC.md). The derived geometry is asserted against
    cyrinx_bulk_compute_geometry so any drift between the two implementations
    fails loudly here instead of decoding garbage."""
    import modem as M  # local: keep clib importable with numpy alone
    rate = cfg.rate.decode() if isinstance(cfg.rate, bytes) else cfg.rate
    kw = dict(f_lo=cfg.f_lo, f_hi=cfg.f_hi, pilot_every=cfg.pilot_every,
              rate=rate, n_sym=cfg.n_sym, amp=cfg.amp,
              clip_sigma=cfg.clip_sigma, nfft=cfg.nfft, cp=cfg.cp, sr=cfg.sr,
              chirp_f0=cfg.chirp_f0, chirp_f1=cfg.chirp_f1)
    m = M.Config(**kw)
    if cfg.bits_per_bin != 2:  # Config defaults to 2 bits/bin; rebuild w/ dict
        m = M.Config(bits_per_bin={int(b): cfg.bits_per_bin for b in m.data_idx},
                     **kw)
    g = geometry(cfg)
    got = (m.bin_lo, m.bin_hi, m.bits_per_sym, m.info_bits, m.n_blocks,
           m.payload_bytes, m.frame_samples)
    want = (g.bin_lo, g.bin_hi, g.bits_per_sym, g.info_bits, g.n_blocks,
            g.payload_bytes, g.frame_samples)
    assert got == want, f"clib/modem geometry mismatch: modem {got} != clib {want}"
    return m


if __name__ == "__main__":
    # digital loopback sanity check through the library C code
    for bpb, rate in [(2, "1/2"), (4, "3/4"), (6, "3/4")]:
        cfg = make_cfg(bits_per_bin=bpb, rate=rate, n_sym=8)
        g = geometry(cfg)
        payload = bytes((i * 31 + 7) & 0xFF for i in range(g.payload_bytes))
        wave = encode(cfg, payload)
        rx = np.concatenate([np.zeros(3000, np.float32), wave, np.zeros(2000, np.float32)])
        r = decode(cfg, rx)
        ok = (r and r["payload"] == payload
              and r["blocks_ok"] == r["blocks_total"]
              and all(r["block_valid"]))
        print(f"  bpb={bpb} rate={rate}: blocks {r['blocks_ok']}/{r['blocks_total']} "
              f"payload_match={r['payload']==payload} -> {'OK' if ok else 'FAIL'}")
