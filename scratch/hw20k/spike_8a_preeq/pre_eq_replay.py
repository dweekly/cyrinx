#!/usr/bin/env python3
"""Offline Rank 8a pre-equalization spike over retained Cyrinx captures.

This is deliberately a research oracle.  It never opens an audio device and it
does not call ADB.  The selected transmit profile is frozen on whole training
runs before held-out result fields are scored.  The counterfactual receiver is
linear: it reuses the measured flat-waveform channel and the exact retained
residual realization.  Consequently, its result is a replay screen rather than
evidence about loudspeaker protection, nonlinear distortion, or acoustic level.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
HW20K_DIR = SCRIPT_DIR.parent
if str(HW20K_DIR) not in sys.path:
    sys.path.insert(0, str(HW20K_DIR))

import clib  # noqa: E402
import modem  # noqa: E402


SCHEMA_VERSION = "cyrinx.spike-8a-preeq.analysis.v1"
PROFILE_ID = "candidate-cp96-p16-b6-r23"
RUN_PATTERN = re.compile(r"pair-(\d{3})-order-([01])-candidate-cp96-p16-b6-r23$")
REGULARIZATION_GRID = (0.01, 0.03, 0.1, 0.3)
BOOST_CAPS_DB = (3.0, 6.0)
PREREGISTRATION_EXPECTED_SHA256 = "6b8f5bd0c47b081f35062343d22c93ee7f2297f86827714d6123bb4f084934e5"
MARGIN_SAMPLES = 1200


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def finite_float(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def db20(value: float | np.ndarray, floor: float = 1e-15) -> float | np.ndarray:
    return 20.0 * np.log10(np.maximum(np.asarray(value), floor))


def db10(value: float | np.ndarray, floor: float = 1e-30) -> float | np.ndarray:
    return 10.0 * np.log10(np.maximum(np.asarray(value), floor))


def percentile(values: Iterable[float], q: float) -> float:
    array = np.asarray(list(values), dtype=float)
    if array.size == 0:
        return float("nan")
    return float(np.percentile(array, q, method="linear"))


def make_configs() -> tuple[modem.Config, clib.Cfg]:
    skeleton = modem.Config(
        f_lo=1100.0,
        f_hi=23000.0,
        pilot_every=16,
        rate="2/3",
        n_sym=64,
        amp=0.18,
        clip_sigma=3.3,
        nfft=2048,
        cp=96,
        sr=48000,
    )
    bits_per_bin = {int(bin_index): 6 for bin_index in skeleton.data_idx}
    py_cfg = modem.Config(
        f_lo=1100.0,
        f_hi=23000.0,
        pilot_every=16,
        bits_per_bin=bits_per_bin,
        rate="2/3",
        n_sym=64,
        amp=0.18,
        clip_sigma=3.3,
        nfft=2048,
        cp=96,
        sr=48000,
    )
    c_cfg = clib.make_cfg(
        bits_per_bin=6,
        rate="2/3",
        n_sym=64,
        f_lo=1100.0,
        f_hi=23000.0,
        nfft=2048,
        cp=96,
        sr=48000,
        amp=0.18,
        clip_sigma=3.3,
        pilot_every=16,
    )
    return py_cfg, c_cfg


@dataclasses.dataclass
class TransmitRealization:
    body: np.ndarray
    spectra_used: np.ndarray
    ideal_spectra: np.ndarray
    stats: dict[str, float]


@dataclasses.dataclass
class FrameObservation:
    campaign: str
    run_id: str
    run_dir: Path
    pair_index: int
    order: int
    frame_index: int
    detected_sample: int
    base_sample: int
    payload: bytes
    truth_bits: np.ndarray
    flat_tx: TransmitRealization
    y_full: np.ndarray
    physical_channel: np.ndarray
    capture: np.ndarray
    result_path: Path

    @property
    def key(self) -> str:
        return f"{self.campaign}:{self.run_id}:frame-{self.frame_index}"


def full_spectra_from_taps(taps: dict[str, Any]) -> np.ndarray:
    return np.concatenate((taps["sync_freq"], taps["data_freq"]), axis=0)


def spectral_energy_fraction(body: np.ndarray, cfg: modem.Config) -> float:
    symbols = body.reshape(2 + cfg.n_sym, cfg.sym)
    spectra = np.fft.rfft(symbols[:, cfg.cp :], axis=1)
    weights = np.ones(spectra.shape[1])
    weights[1:-1] = 2.0
    power = np.abs(spectra) ** 2 * weights[None, :]
    in_band = np.zeros(spectra.shape[1], dtype=bool)
    in_band[cfg.used] = True
    total = float(np.sum(power))
    outside = float(np.sum(power[:, ~in_band]))
    return outside / max(total, 1e-30)


def realize_transmit(
    cfg: modem.Config,
    ideal_spectra: np.ndarray,
    weights: np.ndarray,
    *,
    normalization: str = "equal_peak",
    matched_rms: float | None = None,
) -> TransmitRealization:
    if ideal_spectra.shape != (2 + cfg.n_sym, len(cfg.used)):
        raise ValueError("unexpected ideal-spectrum geometry")
    if weights.shape != (len(cfg.used),):
        raise ValueError("unexpected pre-equalizer geometry")
    weighted = ideal_spectra * weights[None, :]
    raw_symbols = np.stack([modem.ofdm_mod_symbol(cfg, row) for row in weighted])
    raw = raw_symbols.reshape(-1)
    sigma = float(np.std(raw))
    limit = cfg.clip_sigma * sigma
    clipped_mask = np.abs(raw) > limit
    clipped = np.clip(raw, -limit, limit)
    if normalization == "equal_peak":
        scale = cfg.amp / max(float(np.max(np.abs(clipped))), 1e-30)
    elif normalization == "matched_rms":
        if matched_rms is None or matched_rms <= 0.0:
            raise ValueError("matched RMS normalization needs a positive target")
        scale = matched_rms / max(float(np.sqrt(np.mean(clipped**2))), 1e-30)
    else:
        raise ValueError(f"unsupported normalization {normalization}")
    body = clipped * scale
    shaped = body.reshape(2 + cfg.n_sym, cfg.sym)
    actual = np.fft.rfft(shaped[:, cfg.cp :], axis=1)[:, cfg.used]
    rms = float(np.sqrt(np.mean(body**2)))
    peak = float(np.max(np.abs(body)))
    raw_rms = float(np.sqrt(np.mean(raw**2)))
    stats = {
        "raw_crest_factor_db": float(db20(np.max(np.abs(raw)) / max(raw_rms, 1e-30))),
        "post_clip_crest_factor_db": float(db20(peak / max(rms, 1e-30))),
        "raw_sigma": sigma,
        "clip_limit": limit,
        "intentional_clip_fraction": float(np.mean(clipped_mask)),
        "scale": float(scale),
        "body_peak": peak,
        "body_rms": rms,
        "out_of_band_energy_fraction": spectral_energy_fraction(body, cfg),
        "out_of_band_energy_db": float(db10(spectral_energy_fraction(body, cfg))),
    }
    return TransmitRealization(body=body, spectra_used=actual, ideal_spectra=ideal_spectra, stats=stats)


def reconstruct_flat(cfg: modem.Config, payload: bytes) -> tuple[TransmitRealization, dict[str, Any]]:
    taps: dict[str, Any] = {}
    reference = modem.modulate_frame(cfg, payload, taps=taps)
    ideal = full_spectra_from_taps(taps)
    realization = realize_transmit(cfg, ideal, np.ones(len(cfg.used), dtype=complex))
    reconstructed = np.concatenate((cfg.chirp_wave * cfg.amp, np.zeros(modem.GUARD), realization.body))
    if not np.array_equal(reference, reconstructed.astype(np.float32)):
        error = float(np.max(np.abs(reference.astype(float) - reconstructed)))
        raise RuntimeError(f"flat Python waveform reconstruction drifted by {error}")
    return realization, taps


def fine_base(capture_mic0: np.ndarray, detected_sample: int, cfg: modem.Config) -> int:
    nominal = detected_sample + modem.CHIRP_LEN + modem.GUARD
    reference = modem.ofdm_mod_symbol(cfg, modem.sync_symbol_freq(cfg, 0))
    low = max(0, nominal - 400)
    high = min(len(capture_mic0), nominal + 400 + cfg.sym)
    segment = capture_mic0[low:high]
    correlation = np.correlate(segment, reference, mode="valid")
    if correlation.size == 0:
        raise RuntimeError("short capture during fine alignment")
    return low + int(np.argmax(np.abs(correlation))) - 24


def result_run_plan(dry_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["run_id"]: row for row in dry_manifest["plan"]["runs"]}


def discover_runs(root: Path) -> list[tuple[int, int, Path]]:
    runs: list[tuple[int, int, Path]] = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        match = RUN_PATTERN.fullmatch(path.name)
        if match:
            runs.append((int(match.group(1)), int(match.group(2)), path))
    runs.sort()
    if len(runs) != 8 or [row[0] for row in runs] != list(range(8)):
        raise RuntimeError(f"{root} does not contain exactly one candidate arm for each of eight pairs")
    return runs


def load_campaign(
    name: str,
    root: Path,
    dry_manifest_path: Path,
    expected_manifest_sha256: str,
    cfg: modem.Config,
) -> tuple[list[FrameObservation], dict[str, Any]]:
    actual_manifest_hash = sha256_file(dry_manifest_path)
    if actual_manifest_hash != expected_manifest_sha256:
        raise RuntimeError(f"dry-run manifest hash mismatch for {name}")
    dry_manifest = json.loads(dry_manifest_path.read_text())
    plans = result_run_plan(dry_manifest)
    inventory_runs: list[dict[str, Any]] = []
    observations: list[FrameObservation] = []
    for pair_index, order, run_dir in discover_runs(root):
        result_path = run_dir / "result.json"
        result = json.loads(result_path.read_text())
        if result["status"] != "complete" or result["profile_id"] != PROFILE_ID:
            raise RuntimeError(f"incomplete or wrong-profile retained run {run_dir.name}")
        run_plan = plans[run_dir.name]
        tx_path = run_dir / "tx-stereo-f32le.pcm"
        capture_path = run_dir / "capture-stereo-s16le.pcm"
        tx = np.fromfile(tx_path, dtype="<f4").reshape(-1, 2)
        capture = np.fromfile(capture_path, dtype="<i2").reshape(-1, 2).astype(float) / 32768.0
        detections = {int(row["frame_index"]): row for row in result["timing"]["detections"]}
        required_hashes: dict[str, str] = {
            "result.json": sha256_file(result_path),
            "tx-stereo-f32le.pcm": sha256_file(tx_path),
            "capture-stereo-s16le.pcm": sha256_file(capture_path),
        }
        for frame_index in range(5):
            payload_path = run_dir / f"expected-frame-{frame_index}.bin"
            payload = payload_path.read_bytes()
            required_hashes[payload_path.name] = sha256_file(payload_path)
            flat_tx, taps = reconstruct_flat(cfg, payload)
            scheduled_start = int(run_plan["scheduled_frame_starts_tx_samples"][frame_index])
            stop = scheduled_start + cfg.frame_samples
            expected_wave = np.concatenate(
                (cfg.chirp_wave * cfg.amp, np.zeros(modem.GUARD), flat_tx.body)
            ).astype(np.float32)
            if not np.array_equal(tx[scheduled_start:stop, 0], expected_wave):
                raise RuntimeError(f"retained TX mismatch in {run_dir.name} frame {frame_index}")
            if np.any(tx[scheduled_start:stop, 1] != 0.0):
                raise RuntimeError(f"unexpected second-speaker samples in {run_dir.name}")
            detected = int(detections[frame_index]["detected_sample"])
            base = fine_base(capture[:, 0], detected, cfg)
            y_full = np.empty((2, 2 + cfg.n_sym, cfg.nfft // 2 + 1), dtype=complex)
            for mic in range(2):
                for symbol_index in range(2 + cfg.n_sym):
                    position = base + symbol_index * cfg.sym + cfg.cp
                    samples = capture[position : position + cfg.nfft, mic]
                    if len(samples) != cfg.nfft:
                        raise RuntimeError(f"short retained body in {run_dir.name} frame {frame_index}")
                    y_full[mic, symbol_index] = np.fft.rfft(samples)
            y_used = y_full[:, :, cfg.used]
            physical_channel = np.empty((2, len(cfg.used)), dtype=complex)
            for mic in range(2):
                estimates = y_used[mic, :2] / flat_tx.spectra_used[:2]
                physical_channel[mic] = np.mean(estimates, axis=0)
            truth_bits = taps["interleaved_bits"].reshape(cfg.n_sym, -1, 6)
            observations.append(
                FrameObservation(
                    campaign=name,
                    run_id=run_dir.name,
                    run_dir=run_dir,
                    pair_index=pair_index,
                    order=order,
                    frame_index=frame_index,
                    detected_sample=detected,
                    base_sample=base,
                    payload=payload,
                    truth_bits=truth_bits,
                    flat_tx=flat_tx,
                    y_full=y_full,
                    physical_channel=physical_channel,
                    capture=capture,
                    result_path=result_path,
                )
            )
        inventory_runs.append(
            {
                "run_id": run_dir.name,
                "pair_index": pair_index,
                "within_pair_order": order,
                "files": required_hashes,
                "route_signature_sha256": canonical_sha256(
                    result["android_audio_record"]["route_realization"]["route_signature"]
                ),
                "capture_clipped_samples": result["android_audio_record"]["realization"][
                    "clipped_samples"
                ],
            }
        )
    inventory = {
        "campaign": name,
        "root": str(root),
        "dry_run_manifest": str(dry_manifest_path),
        "dry_run_manifest_sha256": actual_manifest_hash,
        "runs": inventory_runs,
        "frames": len(observations),
        "microphones": 2,
    }
    return observations, inventory


def reflected_hann_smooth(values: np.ndarray, width: int = 41) -> np.ndarray:
    if width < 3 or width % 2 == 0:
        raise ValueError("smoothing width must be odd and at least three")
    window = np.hanning(width)
    window /= np.sum(window)
    radius = width // 2
    padded = np.pad(values, (radius, radius), mode="reflect")
    return np.convolve(padded, window, mode="valid")


def minimum_phase_weights(cfg: modem.Config, magnitude: np.ndarray) -> np.ndarray:
    if np.any(magnitude <= 0.0) or magnitude.shape != (len(cfg.used),):
        raise ValueError("minimum-phase reconstruction needs positive occupied-bin magnitudes")
    full_magnitude = np.ones(cfg.nfft)
    full_magnitude[cfg.used] = magnitude
    full_magnitude[(-cfg.used) % cfg.nfft] = magnitude
    cepstrum = np.fft.ifft(np.log(full_magnitude)).real
    minimum_cepstrum = np.zeros_like(cepstrum)
    minimum_cepstrum[0] = cepstrum[0]
    minimum_cepstrum[1 : cfg.nfft // 2] = 2.0 * cepstrum[1 : cfg.nfft // 2]
    minimum_cepstrum[cfg.nfft // 2] = cepstrum[cfg.nfft // 2]
    response = np.exp(np.fft.fft(minimum_cepstrum))
    weights = response[cfg.used]
    weights *= magnitude / np.maximum(np.abs(weights), 1e-30)
    return weights


def design_smooth_profile(
    cfg: modem.Config,
    fit_observations: list[FrameObservation],
    regularization: float,
    cap_db: float,
) -> dict[str, Any]:
    channels = np.concatenate([row.physical_channel for row in fit_observations], axis=0)
    log_magnitude = np.log(np.maximum(np.abs(channels), 1e-30))
    robust_log_magnitude = np.median(log_magnitude, axis=0)
    smooth_log_magnitude = reflected_hann_smooth(robust_log_magnitude, 41)
    smooth_magnitude = np.exp(smooth_log_magnitude)
    median_power = float(np.median(smooth_magnitude**2))
    inverse = smooth_magnitude / (smooth_magnitude**2 + regularization * median_power)
    inverse /= max(float(np.median(inverse)), 1e-30)
    cap_linear = 10.0 ** (cap_db / 20.0)
    inverse = np.clip(inverse, 1.0 / cap_linear, cap_linear)
    weights = minimum_phase_weights(cfg, inverse)
    return {
        "kind": "smooth-magnitude-minimum-phase",
        "regularization": regularization,
        "boost_cap_db": cap_db,
        "weights": weights,
        "smoothed_channel_magnitude": smooth_magnitude,
        "raw_inversion_magnitude": inverse,
    }


def phase_legitimacy(
    cfg: modem.Config, fit_observations: list[FrameObservation]
) -> tuple[dict[str, Any], list[np.ndarray]]:
    by_mic: list[list[np.ndarray]] = [[], []]
    for row in fit_observations:
        for mic in range(2):
            by_mic[mic].append(row.physical_channel[mic])
    aligned_by_mic: list[np.ndarray] = []
    report: dict[str, Any] = {}
    x = np.arange(len(cfg.used), dtype=float)
    for mic, responses_list in enumerate(by_mic):
        responses = np.stack(responses_list)
        reference = responses[0]
        aligned: list[np.ndarray] = []
        for response in responses:
            difference = np.unwrap(np.angle(response * np.conj(reference)))
            slope, intercept = np.polyfit(x, difference, 1)
            corrected = response * np.exp(-1j * (slope * x + intercept))
            corrected /= max(float(np.exp(np.mean(np.log(np.maximum(np.abs(corrected), 1e-30))))), 1e-30)
            aligned.append(corrected)
        aligned_array = np.stack(aligned)
        unit = aligned_array / np.maximum(np.abs(aligned_array), 1e-30)
        coherence = np.abs(np.mean(unit, axis=0))
        circular_std_degrees = np.degrees(
            np.sqrt(np.maximum(-2.0 * np.log(np.maximum(coherence, 1e-30)), 0.0))
        )
        passing = (coherence >= 0.90) & (circular_std_degrees <= 20.0)
        report[f"mic{mic}"] = {
            "passing_bin_fraction": float(np.mean(passing)),
            "coherence_p10": percentile(coherence, 10),
            "phase_std_degrees_p90": percentile(circular_std_degrees, 90),
            "passes": bool(np.mean(passing) >= 0.80),
        }
        aligned_by_mic.append(aligned_array)
    report["eligible"] = bool(report["mic0"]["passes"] and report["mic1"]["passes"])
    report["interpretation"] = (
        "Bulk linear timing and common phase were removed independently per run. Passing this "
        "gate permits only a fixed-route descriptive oracle; it does not establish a reusable "
        "device-only phase calibration."
    )
    return report, aligned_by_mic


def design_complex_oracle(
    cfg: modem.Config,
    aligned_mic0: np.ndarray,
    regularization: float,
    cap_db: float,
) -> dict[str, Any]:
    channel = np.mean(aligned_mic0, axis=0)
    median_power = float(np.median(np.abs(channel) ** 2))
    weights = np.conj(channel) / (np.abs(channel) ** 2 + regularization * median_power)
    weights /= max(float(np.median(np.abs(weights))), 1e-30)
    cap = 10.0 ** (cap_db / 20.0)
    magnitude = np.clip(np.abs(weights), 1.0 / cap, cap)
    weights = weights / np.maximum(np.abs(weights), 1e-30) * magnitude
    return {
        "kind": "fixed-route-mic0-complex-inverse-oracle",
        "regularization": regularization,
        "boost_cap_db": cap_db,
        "weights": weights,
    }


def smooth_endpoint(values: np.ndarray, width: int = 11) -> np.ndarray:
    radius = width // 2
    padded = np.pad(values, (radius, radius), mode="edge")
    return np.convolve(padded, np.ones(width) / width, mode="valid")


_AWGN_TABLE: tuple[np.ndarray, np.ndarray] | None = None


def bicm_gmi(bits: np.ndarray, llrs: np.ndarray) -> float:
    signed = (1.0 - 2.0 * bits.astype(float)) * llrs
    loss = np.logaddexp(0.0, -np.clip(signed, -1000.0, 1000.0)) / math.log(2.0)
    return float(1.0 - np.mean(loss))


def awgn_gmi_table() -> tuple[np.ndarray, np.ndarray]:
    global _AWGN_TABLE
    if _AWGN_TABLE is not None:
        return _AWGN_TABLE
    rng = np.random.default_rng(0x8A20260718)
    count = 32768
    bits = rng.integers(0, 2, size=(count, 6), dtype=np.uint8)
    symbols = modem.qam_map(bits, 6)
    noise_unit = (rng.normal(size=count) + 1j * rng.normal(size=count)) / math.sqrt(2.0)
    snr_db = np.arange(-15.0, 40.0001, 0.25)
    gmi = []
    for level_db in snr_db:
        n0 = 10.0 ** (-level_db / 10.0)
        received = symbols + math.sqrt(n0) * noise_unit
        llr = modem.qam_llr(received, 6, n0)
        gmi.append(max(0.0, min(1.0, bicm_gmi(bits, llr))))
    gmi_array = np.maximum.accumulate(np.asarray(gmi))
    _AWGN_TABLE = snr_db, gmi_array
    return _AWGN_TABLE


def equivalent_awgn_sinr_db(gmi: float) -> float:
    snr, table = awgn_gmi_table()
    clipped = min(max(gmi, float(table[0])), float(table[-1]))
    return float(np.interp(clipped, table, snr))


def counterfactual_used(
    observation: FrameObservation, candidate_tx: TransmitRealization
) -> np.ndarray:
    """Apply the candidate to a channel tracked only by known sync/pilots.

    Treating every difference from the frame-start channel as additive noise is
    not phase-invariant: ordinary per-symbol CPE/timing drift would remain
    multiplied by the *flat* QAM phasor and become artificial interference as
    soon as a complex pre-equalizer rotates that phasor.  The preregistration
    instead permits known sync and pilot observations.  We therefore carry the
    frame-start per-bin response forward with a per-symbol scalar amplitude,
    CPE, and linear phase slope fitted only from the comb pilots.  Everything
    not described by that causal model remains the exact retained residual.
    """
    cfg = observation_cfg()
    y_used = observation.y_full[:, :, cfg.used]
    flat_spectra = observation.flat_tx.spectra_used
    channel_grid = np.repeat(
        observation.physical_channel[:, None, :], 2 + cfg.n_sym, axis=1
    )
    pilot_positions = np.arange(0, len(cfg.used), 16)
    pilot_bins = cfg.used[pilot_positions].astype(float)
    for mic in range(2):
        sync_estimates = y_used[mic, :2] / flat_spectra[:2]
        sync_noise = (
            np.convolve(
                np.abs(sync_estimates[0] - sync_estimates[1]) ** 2 / 2.0,
                np.ones(9) / 9.0,
                mode="same",
            )
            + 1e-12
        )
        sync_snr = np.abs(observation.physical_channel[mic]) ** 2 / sync_noise
        pilot_weights = modem.pilot_phase_weights(sync_snr[pilot_positions])
        for symbol_index in range(cfg.n_sym):
            physical_pilots = (
                y_used[mic, 2 + symbol_index, pilot_positions]
                / flat_spectra[2 + symbol_index, pilot_positions]
            )
            relative = physical_pilots / np.where(
                np.abs(observation.physical_channel[mic, pilot_positions]) > 1e-30,
                observation.physical_channel[mic, pilot_positions],
                1e-30 + 0j,
            )
            slope, phase = modem.fit_pilot_phase(relative, pilot_bins, pilot_weights)
            amplitude = float(np.median(np.abs(relative)))
            amplitude = min(max(amplitude, 0.5), 2.0)
            evolution = amplitude * np.exp(
                1j * (phase + slope * (cfg.used - pilot_bins[0]))
            )
            channel_grid[mic, 2 + symbol_index] *= evolution
    residual = y_used - channel_grid * flat_spectra[None, :, :]
    return channel_grid * candidate_tx.spectra_used[None, :, :] + residual


_CONFIG_CACHE: tuple[modem.Config, clib.Cfg] | None = None


def observation_cfg() -> modem.Config:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = make_configs()
    return _CONFIG_CACHE[0]


def evaluate_observation(
    observation: FrameObservation, candidate_tx: TransmitRealization
) -> list[dict[str, Any]]:
    cfg = observation_cfg()
    y_candidate = counterfactual_used(observation, candidate_tx)
    ideal_sync = np.stack([modem.sync_symbol_freq(cfg, index) for index in range(2)])
    used_positions = np.arange(len(cfg.used))
    pilot_positions = np.arange(0, len(cfg.used), 16)
    data_positions = np.asarray([index for index in used_positions if index % 16 != 0])
    pilot_bins = cfg.used[pilot_positions].astype(float)
    rows: list[dict[str, Any]] = []
    for mic in range(2):
        h_sync = y_candidate[mic, :2] / ideal_sync
        channel = np.mean(h_sync, axis=0)
        raw_noise = np.abs(h_sync[0] - h_sync[1]) ** 2 / 2.0
        noise_variance = np.convolve(raw_noise, np.ones(9) / 9.0, mode="same") + 1e-12
        sync_snr = np.abs(channel) ** 2 / noise_variance
        pilot_weights = modem.pilot_phase_weights(sync_snr[pilot_positions])
        all_llrs: list[np.ndarray] = []
        all_bits: list[np.ndarray] = []
        evm_values: list[float] = []
        hard_errors = 0
        total_bits = 0
        for symbol_index in range(cfg.n_sym):
            z = y_candidate[mic, 2 + symbol_index] / np.where(
                np.abs(channel) > 1e-30, channel, 1e-30 + 0j
            )
            phase_error = z[pilot_positions] * np.conj(cfg.pilots)
            slope, phase = modem.fit_pilot_phase(phase_error, pilot_bins, pilot_weights)
            correction = np.exp(-1j * (phase + slope * (cfg.used - pilot_bins[0])))
            z *= correction
            pilot_error = np.abs(z[pilot_positions] * np.conj(cfg.pilots) - 1.0) ** 2
            global_evm2 = float(np.mean(pilot_error))
            local_pilots = smooth_endpoint(pilot_error, 11)
            local_evm2 = np.interp(used_positions, pilot_positions, local_pilots)[data_positions]
            n0 = (
                1.0 / np.maximum(sync_snr[data_positions], 0.1)
                + 0.25 * global_evm2
                + 0.75 * local_evm2
            )
            llr = modem.qam_llr(z[data_positions], 6, n0)
            truth = observation.truth_bits[symbol_index]
            all_llrs.append(llr)
            all_bits.append(truth)
            hard_errors += int(np.count_nonzero((llr < 0.0) != truth))
            total_bits += int(truth.size)
            evm_values.append(math.sqrt(global_evm2))
        llrs = np.concatenate(all_llrs, axis=0)
        bits = np.concatenate(all_bits, axis=0)
        gmi = max(0.0, min(1.0, bicm_gmi(bits, llrs)))
        rows.append(
            {
                "key": observation.key,
                "campaign": observation.campaign,
                "run_id": observation.run_id,
                "pair_index": observation.pair_index,
                "order": observation.order,
                "frame_index": observation.frame_index,
                "microphone": f"mic{mic}",
                "bicm_gmi_bits_per_coded_bit": gmi,
                "bicm_gmi_bits_per_data_carrier": 6.0 * gmi,
                "effective_sinr_db": equivalent_awgn_sinr_db(gmi),
                "uncoded_hard_bit_error_rate": hard_errors / max(total_bits, 1),
                "pilot_evm_rms": float(np.mean(evm_values)),
            }
        )
    return rows


def profile_transmit(
    observation: FrameObservation,
    weights: np.ndarray,
    normalization: str = "equal_peak",
) -> TransmitRealization:
    matched_rms = observation.flat_tx.stats["body_rms"] if normalization == "matched_rms" else None
    return realize_transmit(
        observation_cfg(),
        observation.flat_tx.ideal_spectra,
        weights,
        normalization=normalization,
        matched_rms=matched_rms,
    )


def aggregate_profile(
    observations: list[FrameObservation],
    weights: np.ndarray,
    normalization: str = "equal_peak",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    tx_stats: list[dict[str, float]] = []
    for observation in observations:
        tx = profile_transmit(observation, weights, normalization)
        metrics.extend(evaluate_observation(observation, tx))
        tx_stats.append(tx.stats)
    summary = {
        "normalization": normalization,
        "frames": len(observations),
        "frame_microphone_units": len(metrics),
        "p10_effective_sinr_db": percentile((row["effective_sinr_db"] for row in metrics), 10),
        "median_effective_sinr_db": percentile((row["effective_sinr_db"] for row in metrics), 50),
        "p10_bicm_gmi": percentile((row["bicm_gmi_bits_per_coded_bit"] for row in metrics), 10),
        "median_bicm_gmi": percentile((row["bicm_gmi_bits_per_coded_bit"] for row in metrics), 50),
        "max_body_peak": max(row["body_peak"] for row in tx_stats),
        "median_body_rms": percentile((row["body_rms"] for row in tx_stats), 50),
        "p99_intentional_clip_fraction": percentile(
            (row["intentional_clip_fraction"] for row in tx_stats), 99
        ),
        "p99_raw_crest_factor_db": percentile((row["raw_crest_factor_db"] for row in tx_stats), 99),
        "p99_post_clip_crest_factor_db": percentile(
            (row["post_clip_crest_factor_db"] for row in tx_stats), 99
        ),
        "median_out_of_band_energy_db": percentile(
            (row["out_of_band_energy_db"] for row in tx_stats), 50
        ),
    }
    for mic in ("mic0", "mic1"):
        selected = [row for row in metrics if row["microphone"] == mic]
        summary[mic] = {
            "p10_effective_sinr_db": percentile(
                (row["effective_sinr_db"] for row in selected), 10
            ),
            "median_effective_sinr_db": percentile(
                (row["effective_sinr_db"] for row in selected), 50
            ),
            "p10_bicm_gmi": percentile(
                (row["bicm_gmi_bits_per_coded_bit"] for row in selected), 10
            ),
            "median_bicm_gmi": percentile(
                (row["bicm_gmi_bits_per_coded_bit"] for row in selected), 50
            ),
        }
    return metrics, summary


def compare_summaries(candidate: dict[str, Any], flat: dict[str, Any]) -> dict[str, Any]:
    comparison = {
        "p10_effective_sinr_gain_db": candidate["p10_effective_sinr_db"]
        - flat["p10_effective_sinr_db"],
        "median_effective_sinr_gain_db": candidate["median_effective_sinr_db"]
        - flat["median_effective_sinr_db"],
        "p10_bicm_gmi_delta": candidate["p10_bicm_gmi"] - flat["p10_bicm_gmi"],
        "median_bicm_gmi_delta": candidate["median_bicm_gmi"] - flat["median_bicm_gmi"],
        "out_of_band_energy_delta_db": candidate["median_out_of_band_energy_db"]
        - flat["median_out_of_band_energy_db"],
        "peak_delta": candidate["max_body_peak"] - flat["max_body_peak"],
    }
    for mic in ("mic0", "mic1"):
        comparison[mic] = {
            "p10_effective_sinr_gain_db": candidate[mic]["p10_effective_sinr_db"]
            - flat[mic]["p10_effective_sinr_db"],
            "median_effective_sinr_gain_db": candidate[mic]["median_effective_sinr_db"]
            - flat[mic]["median_effective_sinr_db"],
            "p10_bicm_gmi_delta": candidate[mic]["p10_bicm_gmi"] - flat[mic]["p10_bicm_gmi"],
            "median_bicm_gmi_delta": candidate[mic]["median_bicm_gmi"]
            - flat[mic]["median_bicm_gmi"],
        }
    return comparison


def serialize_profile(candidate: dict[str, Any], cfg: modem.Config) -> dict[str, Any]:
    weights = candidate["weights"]
    magnitude = np.abs(weights)
    return {
        "schema": "cyrinx.spike-8a-preeq.profile.v1",
        "profile_id": candidate["profile_id"],
        "kind": candidate["kind"],
        "regularization": candidate["regularization"],
        "boost_cap_db": candidate["boost_cap_db"],
        "nfft": cfg.nfft,
        "sample_rate_hz": cfg.sr,
        "bin_lo": int(cfg.used[0]),
        "bin_hi": int(cfg.used[-1]),
        "weight_count": len(weights),
        "weight_magnitude_db": [float(value) for value in db20(magnitude)],
        "weight_phase_radians": [float(value) for value in np.angle(weights)],
        "maximum_inversion_gain_db": float(np.max(db20(magnitude))),
        "p99_inversion_gain_db": percentile(db20(magnitude), 99),
        "minimum_inversion_gain_db": float(np.min(db20(magnitude))),
    }


def actual_outcomes(observations: list[FrameObservation]) -> dict[str, Any]:
    result_cache: dict[Path, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    policies = ("mic0", "mic1", "mrc01", "pilot-select01-v1")
    for observation in observations:
        if observation.result_path not in result_cache:
            result_cache[observation.result_path] = json.loads(observation.result_path.read_text())
        result = result_cache[observation.result_path]
        for policy in policies:
            match = next(
                row
                for row in result["decoded_diagnostics"][policy]
                if int(row["frame_index"]) == observation.frame_index
            )
            rows.append(
                {
                    "key": observation.key,
                    "policy": policy,
                    "verified_crc_blocks": int(match["blocks_ok"]),
                    "total_blocks": int(match["blocks_total"]),
                    "block_valid": list(match["block_valid"]),
                }
            )
    by_policy: dict[str, Any] = {}
    campaign_run_count = len({(row.campaign, row.run_id) for row in observations})
    total_schedule_seconds = campaign_run_count * 16.38
    for policy in policies:
        selected = [row for row in rows if row["policy"] == policy]
        total = sum(row["verified_crc_blocks"] for row in selected)
        denominator = sum(row["total_blocks"] for row in selected)
        by_policy[policy] = {
            "verified_crc_blocks": total,
            "total_blocks": denominator,
            "block_recovery": total / max(denominator, 1),
            "scheduled_goodput_bps": total * 256 * 8 / max(total_schedule_seconds, 1e-30),
        }
    return {
        "rows": rows,
        "by_policy": by_policy,
        "total_schedule_seconds": total_schedule_seconds,
    }


def synthetic_crop(
    observation: FrameObservation,
    y_candidate_used: np.ndarray,
    mic: int,
    cfg: modem.Config,
) -> np.ndarray:
    start = max(0, observation.detected_sample - MARGIN_SAMPLES)
    stop = min(
        len(observation.capture),
        observation.detected_sample + cfg.frame_samples + MARGIN_SAMPLES,
    )
    crop = observation.capture[start:stop, mic].copy()
    for symbol_index in range(2 + cfg.n_sym):
        full = observation.y_full[mic, symbol_index].copy()
        full[cfg.used] = y_candidate_used[mic, symbol_index]
        time = np.fft.irfft(full, cfg.nfft)
        with_cp = np.concatenate((time[-cfg.cp :], time))
        position = observation.base_sample + symbol_index * cfg.sym - start
        if position < 0 or position + cfg.sym > len(crop):
            raise RuntimeError("synthetic replay crop is short")
        crop[position : position + cfg.sym] = with_cp
    return crop.astype(np.float32)


def decode_projection(
    observations: list[FrameObservation],
    weights: np.ndarray,
    c_cfg: clib.Cfg,
    *,
    calibration_pairs: set[int],
) -> dict[str, Any]:
    codec_cache: dict[Path, clib.BulkCodec] = {}
    actual = actual_outcomes(observations)
    actual_map = {(row["key"], row["policy"]): row for row in actual["rows"]}
    calibration_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for observation in observations:
        decoder_paths = sorted((observation.run_dir.parent / "decoder").glob("*.dylib"))
        if len(decoder_paths) != 1:
            raise RuntimeError(f"expected one retained decoder in {observation.run_dir.parent}")
        decoder_path = decoder_paths[0]
        codec = codec_cache.setdefault(decoder_path, clib.BulkCodec(decoder_path))
        flat_y = counterfactual_used(observation, observation.flat_tx)
        candidate_tx = profile_transmit(observation, weights)
        candidate_y = counterfactual_used(observation, candidate_tx)
        flat_crops = [
            synthetic_crop(observation, flat_y, mic, observation_cfg()) for mic in range(2)
        ]
        candidate_crops = [
            synthetic_crop(observation, candidate_y, mic, observation_cfg()) for mic in range(2)
        ]
        candidate_decodes = {
            "mic0": codec.decode(c_cfg, candidate_crops[0]),
            "mic1": codec.decode(c_cfg, candidate_crops[1]),
            "mrc01": codec.decode2(c_cfg, candidate_crops[0], candidate_crops[1]),
            "pilot-select01-v1": codec.decode2_auto_v1(
                c_cfg, candidate_crops[0], candidate_crops[1]
            ),
        }
        if observation.pair_index in calibration_pairs:
            flat_decodes = {
                "mic0": codec.decode(c_cfg, flat_crops[0]),
                "mic1": codec.decode(c_cfg, flat_crops[1]),
                "mrc01": codec.decode2(c_cfg, flat_crops[0], flat_crops[1]),
                "pilot-select01-v1": codec.decode2_auto_v1(
                    c_cfg, flat_crops[0], flat_crops[1]
                ),
            }
        else:
            flat_decodes = {}
        for policy in ("mic0", "mic1", "mrc01", "pilot-select01-v1"):
            if observation.pair_index in calibration_pairs:
                decoded_flat = flat_decodes[policy]
                expected = actual_map[(observation.key, policy)]
                got_mask = (
                    [False] * len(expected["block_valid"])
                    if decoded_flat is None
                    else decoded_flat["block_valid"]
                )
                calibration_rows.append(
                    {
                        "key": observation.key,
                        "policy": policy,
                        "exact_mask_match": got_mask == expected["block_valid"],
                        "matching_block_positions": sum(
                            left == right for left, right in zip(got_mask, expected["block_valid"])
                        ),
                        "total_block_positions": len(expected["block_valid"]),
                    }
                )
            decoded_candidate = candidate_decodes[policy]
            verified = (
                0
                if decoded_candidate is None
                else clib.ordered_verified_blocks(decoded_candidate, observation.payload)
            )
            candidate_rows.append(
                {
                    "key": observation.key,
                    "policy": policy,
                    "ordered_verified_blocks": verified,
                    "total_blocks": 107,
                }
            )
    matching_positions = sum(row["matching_block_positions"] for row in calibration_rows)
    total_positions = sum(row["total_block_positions"] for row in calibration_rows)
    exact_frames = sum(row["exact_mask_match"] for row in calibration_rows)
    qualified = bool(calibration_rows) and exact_frames == len(calibration_rows)
    candidate_by_policy: dict[str, Any] = {}
    for policy in ("mic0", "mic1", "mrc01", "pilot-select01-v1"):
        selected = [row for row in candidate_rows if row["policy"] == policy]
        projected_verified = sum(row["ordered_verified_blocks"] for row in selected)
        projected_total = sum(row["total_blocks"] for row in selected)
        candidate_by_policy[policy] = {
            "ordered_verified_blocks": projected_verified,
            "total_blocks": projected_total,
            "projected_block_recovery": projected_verified / max(projected_total, 1),
            "scheduled_goodput_bps": projected_verified
            * 256
            * 8
            / max(actual["total_schedule_seconds"], 1e-30),
        }
    return {
        "model": "frequency-domain LTI replacement followed by the exact retained C decoder",
        "calibration": {
            "whole_pair_indices": sorted(calibration_pairs),
            "frames_microphones": len(calibration_rows),
            "exact_mask_matches": exact_frames,
            "block_position_agreement": matching_positions / max(total_positions, 1),
            "passes_exact_gate": qualified,
        },
        "candidate_projection_status": "available" if qualified else "withheld-calibration-failed",
        "candidate_projection": (
            {
                "by_policy": candidate_by_policy,
                "rows": candidate_rows,
            }
            if qualified
            else None
        ),
        "actual_flat": actual["by_policy"],
        "limitation": (
            "Even a calibrated replay decode is a linear counterfactual, not an OTA "
            "byte/block result."
        ),
    }


def selftests(cfg: modem.Config) -> dict[str, Any]:
    rng = np.random.default_rng(0x8A)
    magnitude = np.exp(rng.normal(0.0, 0.2, len(cfg.used)))
    weights = minimum_phase_weights(cfg, magnitude)
    minphase_error = float(np.max(np.abs(np.abs(weights) - magnitude)))
    payload = rng.integers(0, 256, cfg.payload_bytes, dtype=np.uint8).tobytes()
    flat, taps = reconstruct_flat(cfg, payload)
    flat_again = realize_transmit(cfg, full_spectra_from_taps(taps), np.ones(len(cfg.used), complex))
    flat_error = float(np.max(np.abs(flat.body - flat_again.body)))
    bits = rng.integers(0, 2, size=(4096, 6), dtype=np.uint8)
    symbols = modem.qam_map(bits, 6)
    low_noise = (rng.normal(size=len(symbols)) + 1j * rng.normal(size=len(symbols))) * 0.01
    high_noise = (rng.normal(size=len(symbols)) + 1j * rng.normal(size=len(symbols))) * 0.20
    low_gmi = bicm_gmi(bits, modem.qam_llr(symbols + low_noise, 6, 2.0 * 0.01**2))
    high_gmi = bicm_gmi(bits, modem.qam_llr(symbols + high_noise, 6, 2.0 * 0.20**2))
    checks = {
        "minimum_phase_magnitude_error_below_1e-10": minphase_error < 1e-10,
        "flat_realization_exact": flat_error == 0.0,
        "gmi_monotonic_with_noise": low_gmi > high_gmi,
        "awgn_mapping_monotonic": equivalent_awgn_sinr_db(low_gmi)
        > equivalent_awgn_sinr_db(high_gmi),
    }
    return {
        "schema": "cyrinx.spike-8a-preeq.selftest.v1",
        "passed": all(checks.values()),
        "checks": checks,
        "measurements": {
            "minimum_phase_magnitude_max_error": minphase_error,
            "flat_body_max_error": flat_error,
            "low_noise_gmi": low_gmi,
            "high_noise_gmi": high_gmi,
        },
    }


def profile_candidate_id(kind: str, regularization: float, cap_db: float) -> str:
    prefix = "smooth-minphase" if kind.startswith("smooth") else "complex-oracle"
    return f"{prefix}-lambda{regularization:g}-cap{cap_db:g}db"


def subset(observations: list[FrameObservation], pairs: Iterable[int]) -> list[FrameObservation]:
    selected = set(pairs)
    return [row for row in observations if row.pair_index in selected]


def freeze_candidate(
    cfg: modem.Config,
    fit: list[FrameObservation],
    phase_report: dict[str, Any],
    aligned: list[np.ndarray],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    flat_weights = np.ones(len(cfg.used), dtype=complex)
    _, flat_summary = aggregate_profile(fit, flat_weights)
    candidates: list[dict[str, Any]] = []
    for regularization in REGULARIZATION_GRID:
        for cap_db in BOOST_CAPS_DB:
            candidate = design_smooth_profile(cfg, fit, regularization, cap_db)
            candidate["profile_id"] = profile_candidate_id(
                candidate["kind"], regularization, cap_db
            )
            _, summary = aggregate_profile(fit, candidate["weights"])
            comparison = compare_summaries(summary, flat_summary)
            valid = (
                summary["max_body_peak"] <= cfg.amp + 1e-12
                and comparison["out_of_band_energy_delta_db"] <= 0.25
                and np.all(np.isfinite(candidate["weights"]))
            )
            candidates.append(
                candidate
                | {
                    "fit_summary": summary,
                    "fit_comparison": comparison,
                    "valid": bool(valid),
                }
            )
    valid_candidates = [row for row in candidates if row["valid"]]
    if not valid_candidates:
        raise RuntimeError("every preregistered smooth profile violated a fit non-regression gate")
    selected = max(
        valid_candidates,
        key=lambda row: (
            row["fit_summary"]["p10_bicm_gmi"],
            -float(np.max(np.abs(row["weights"]))),
            -row["fit_summary"]["p99_raw_crest_factor_db"],
            -row["boost_cap_db"],
        ),
    )
    oracle_result: dict[str, Any] | None = None
    if phase_report["eligible"]:
        oracle_candidates: list[dict[str, Any]] = []
        for regularization in REGULARIZATION_GRID:
            for cap_db in BOOST_CAPS_DB:
                oracle = design_complex_oracle(
                    cfg, aligned[0], regularization, cap_db
                )
                oracle["profile_id"] = profile_candidate_id(
                    oracle["kind"], regularization, cap_db
                )
                _, summary = aggregate_profile(fit, oracle["weights"])
                oracle_candidates.append(
                    oracle
                    | {
                        "fit_summary": summary,
                        "fit_comparison": compare_summaries(summary, flat_summary),
                    }
                )
        oracle_result = max(
            oracle_candidates,
            key=lambda row: row["fit_summary"]["p10_bicm_gmi"],
        )
    compact_candidates = [
        {
            "profile_id": row["profile_id"],
            "kind": row["kind"],
            "regularization": row["regularization"],
            "boost_cap_db": row["boost_cap_db"],
            "valid": row["valid"],
            "fit_summary": row["fit_summary"],
            "fit_comparison": row["fit_comparison"],
            "maximum_inversion_gain_db": float(np.max(db20(np.abs(row["weights"])))),
            "p99_inversion_gain_db": percentile(db20(np.abs(row["weights"])), 99),
        }
        for row in candidates
    ]
    return selected, compact_candidates, oracle_result


def evaluate_subset(
    name: str,
    observations: list[FrameObservation],
    candidate_weights: np.ndarray,
) -> dict[str, Any]:
    flat_weights = np.ones(len(observation_cfg().used), dtype=complex)
    flat_metrics, flat_summary = aggregate_profile(observations, flat_weights)
    candidate_metrics, candidate_summary = aggregate_profile(observations, candidate_weights)
    _, matched_summary = aggregate_profile(observations, candidate_weights, "matched_rms")
    return {
        "subset": name,
        "pairs": sorted({row.pair_index for row in observations}),
        "flat": flat_summary,
        "candidate_equal_peak": candidate_summary,
        "candidate_vs_flat": compare_summaries(candidate_summary, flat_summary),
        "candidate_matched_rms_diagnostic": matched_summary,
        "per_frame_microphone": {
            "flat": flat_metrics,
            "candidate_equal_peak": candidate_metrics,
        },
    }


def gate_decision(
    screen: dict[str, Any],
    confirmation: dict[str, Any],
    external: dict[str, Any],
) -> dict[str, Any]:
    screen_delta = screen["candidate_vs_flat"]
    confirmation_delta = confirmation["candidate_vs_flat"]
    external_delta = external["candidate_vs_flat"]
    screen_checks = {
        "aggregate_p10_sinr_gain_at_least_1db": screen_delta[
            "p10_effective_sinr_gain_db"
        ]
        >= 1.0,
        "mic0_p10_gmi_nonnegative": screen_delta["mic0"]["p10_bicm_gmi_delta"] >= 0.0,
        "mic1_p10_gmi_nonnegative": screen_delta["mic1"]["p10_bicm_gmi_delta"] >= 0.0,
        "mic0_median_sinr_nonnegative": screen_delta["mic0"][
            "median_effective_sinr_gain_db"
        ]
        >= 0.0,
        "mic1_median_sinr_nonnegative": screen_delta["mic1"][
            "median_effective_sinr_gain_db"
        ]
        >= 0.0,
        "oob_delta_at_most_0_25db": screen_delta["out_of_band_energy_delta_db"] <= 0.25,
        "equal_peak_nonregression": screen_delta["peak_delta"] <= 1e-12,
    }
    confirmation_checks = {
        "aggregate_p10_sinr_gain_at_least_1db": confirmation_delta[
            "p10_effective_sinr_gain_db"
        ]
        >= 1.0,
        "mic0_p10_gmi_nonnegative": confirmation_delta["mic0"]["p10_bicm_gmi_delta"]
        >= 0.0,
        "mic1_p10_gmi_nonnegative": confirmation_delta["mic1"]["p10_bicm_gmi_delta"]
        >= 0.0,
        "external_aggregate_p10_gmi_nonnegative": external_delta["p10_bicm_gmi_delta"]
        >= 0.0,
        "external_mic0_p10_gmi_nonnegative": external_delta["mic0"]["p10_bicm_gmi_delta"]
        >= 0.0,
        "external_mic1_p10_gmi_nonnegative": external_delta["mic1"]["p10_bicm_gmi_delta"]
        >= 0.0,
    }
    screen_passed = all(screen_checks.values())
    confirmation_passed = screen_passed and all(confirmation_checks.values())
    roadmap_signal = (
        confirmation_delta["p10_effective_sinr_gain_db"] >= 3.0
        or confirmation_delta["p10_bicm_gmi_delta"] >= 0.10
    )
    failed = [
        f"screen:{name}" for name, passed in screen_checks.items() if not passed
    ] + [
        f"confirmation:{name}"
        for name, passed in confirmation_checks.items()
        if not passed
    ]
    return {
        "screen_passed": screen_passed,
        "offline_confirmation_passed": confirmation_passed,
        "roadmap_stage_one_linear_signal": bool(roadmap_signal),
        "screen_checks": screen_checks,
        "confirmation_checks": confirmation_checks,
        "failed_gates": failed,
        "decision": (
            "offline-linear-screen-passed-ota-required"
            if confirmation_passed
            else "stopped-preregistered-offline-gate-not-met"
        ),
        "library_integration": "forbidden; no Sources changes and OTA evidence remains mandatory",
    }


def acquisition_manifest(
    prereg_sha: str,
    profile_path: Path,
    profile_sha: str,
    decision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "cyrinx.spike-8a-preeq.ota-acquisition.v1",
        "state": "ready-for-hardware-agent-review",
        "preregistration_sha256": prereg_sha,
        "offline_decision": decision["decision"],
        "candidate_profile": {
            "path": str(profile_path),
            "sha256": profile_sha,
        },
        "hard_preconditions": [
            "exclusive bench ownership and user acoustic authorization",
            "Pixel 7a UNPROCESSED stereo 48 kHz route signature matches the retained corpus",
            "Mac built-in left speaker only; Elgato Wave is excluded",
            "flat and candidate waveforms are verified to peak at or below 0.18 at macOS volume 50",
            "capture clipping stop remains enabled",
            "candidate weights are frozen before playback",
        ],
        "cells": [
            {"cell_id": "centered-optimal", "lateral_offset_mm": 0},
            {"cell_id": "left-offset", "lateral_offset_mm": -20},
            {"cell_id": "right-offset", "lateral_offset_mm": 20},
        ],
        "screen": {
            "balanced_pairs_per_cell": 2,
            "profiles": ["flat", "frozen-pre-eq"],
            "order": "one flat-first and one pre-eq-first pair per cell",
            "frames_per_run": 5,
            "inter_frame_gap_samples": 12000,
            "stop_if": [
                "capture clipping or route mismatch",
                "candidate has less than 1 dB p10 probe gain",
                "candidate scheduled goodput or either-mic lower-tail regresses",
                "audible level or protection behavior changes unexpectedly",
            ],
        },
        "confirmatory": {
            "maximum_balanced_pairs": 8,
            "promotion_rule": "at least 7/8 paired wins plus ROADMAP Spike 8a metric/recovery gates",
        },
        "required_retained_artifacts": [
            "dry-run manifest and exact order",
            "profile and code hashes",
            "route provenance",
            "flat and pre-EQ TX PCM",
            "raw stereo capture PCM",
            "per-frame ordered block maps and strict byte verification",
            "crest factor, clip counts, out-of-band spectrum, and protection/nonlinearity observations",
            "calibrated LAeq/LCpeak/SPL only if suitable instrumentation is present",
        ],
        "runner_contract": {
            "sample_rate_hz": 48000,
            "nfft": 2048,
            "cp_samples": 96,
            "pilot_every": 16,
            "bits_per_bin": 6,
            "code_rate": "2/3",
            "data_symbols": 64,
            "f_lo_hz": 1100.0,
            "f_hi_hz": 23000.0,
            "waveform_peak": 0.18,
            "mac_output_volume_percent": 50,
            "profile_application": (
                "multiply every sync/pilot/data occupied-bin complex symbol by the "
                "frozen weights before the existing 3.3-sigma clip and equal-peak "
                "normalization; leave the chirp unchanged"
            ),
        },
        "waveform_builder": {
            "script": "scratch/hw20k/spike_8a_preeq/emit_pre_eq_burst.py",
            "argv_template": [
                ".venv/bin/python",
                "scratch/hw20k/spike_8a_preeq/emit_pre_eq_burst.py",
                "--profile",
                "scratch/hw20k/spike_8a_preeq/results/profile.json",
                "--payload",
                "PAYLOAD_FRAME_0",
                "--payload",
                "PAYLOAD_FRAME_1",
                "--payload",
                "PAYLOAD_FRAME_2",
                "--payload",
                "PAYLOAD_FRAME_3",
                "--payload",
                "PAYLOAD_FRAME_4",
                "--output",
                "OUTPUT_STEREO_F32LE_PCM",
                "--metadata-output",
                "OUTPUT_METADATA_JSON",
                "--inter-frame-gap-samples",
                "12000",
                "--trailing-pad-samples",
                "16000",
            ],
            "side_effect_boundary": "writes PCM and metadata only; never opens audio or invokes ADB",
        },
        "note": (
            "The frozen candidate burst is directly buildable, but the current "
            "goodput_campaign CLI has no pre-EQ profile option. A hardware agent must "
            "feed the generated stereo PCM through the existing playback/capture "
            "transaction and retain the ordinary strict scorer outputs without changing "
            "Sources."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=SCRIPT_DIR / "preregistration.json",
    )
    parser.add_argument("--development-root", type=Path)
    parser.add_argument("--external-root", type=Path)
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR / "results")
    parser.add_argument("--selftest-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg, c_cfg = make_configs()
    global _CONFIG_CACHE
    _CONFIG_CACHE = (cfg, c_cfg)
    selftest_result = selftests(cfg)
    write_json(args.output_dir / "selftest.json", selftest_result)
    if not selftest_result["passed"]:
        raise RuntimeError("pre-EQ deterministic selftest failed")
    if args.selftest_only:
        return 0

    prereg_sha = sha256_file(args.preregistration)
    if prereg_sha != PREREGISTRATION_EXPECTED_SHA256:
        raise RuntimeError(
            f"preregistration changed: {prereg_sha} != {PREREGISTRATION_EXPECTED_SHA256}"
        )
    prereg = json.loads(args.preregistration.read_text())
    development_spec = prereg["retained_corpus"]["development_campaign"]
    external_spec = prereg["retained_corpus"]["external_stability_campaign"]
    development_root = args.development_root or Path(development_spec["root"])
    external_root = args.external_root or Path(external_spec["root"])

    development, development_inventory = load_campaign(
        "development",
        development_root,
        Path(development_spec["dry_run_manifest"]),
        development_spec["dry_run_manifest_sha256"],
        cfg,
    )
    external, external_inventory = load_campaign(
        "external-stability",
        external_root,
        Path(external_spec["dry_run_manifest"]),
        external_spec["dry_run_manifest_sha256"],
        cfg,
    )
    inventory = {
        "schema": "cyrinx.spike-8a-preeq.inventory.v1",
        "preregistration_sha256": prereg_sha,
        "development": development_inventory,
        "external_stability": external_inventory,
        "limitations": prereg["retained_corpus"]["known_limitations"],
    }
    write_json(args.output_dir / "inventory.json", inventory)

    split = development_spec["whole_run_split"]
    fit = subset(development, split["fit_and_profile_selection_pair_indices"])
    screen_observations = subset(development, split["held_out_two_pair_screen_pair_indices"])
    confirmation_observations = subset(
        development, split["held_out_two_pair_confirmation_pair_indices"]
    )
    phase_report, aligned = phase_legitimacy(cfg, fit)
    selected, candidate_table, oracle = freeze_candidate(cfg, fit, phase_report, aligned)

    profile_document = serialize_profile(selected, cfg) | {
        "preregistration_sha256": prereg_sha,
        "selection_data": {
            "campaign": "development",
            "whole_pair_indices": split["fit_and_profile_selection_pair_indices"],
            "held_out_pair_indices_not_inspected_during_selection": sorted(
                split["held_out_two_pair_screen_pair_indices"]
                + split["held_out_two_pair_confirmation_pair_indices"]
            ),
        },
        "fit_summary": selected["fit_summary"],
        "fit_comparison": selected["fit_comparison"],
    }
    profile_path = args.output_dir / "profile.json"
    write_json(profile_path, profile_document)
    profile_sha = sha256_file(profile_path)

    screen_result = evaluate_subset("held-out-two-pair-screen", screen_observations, selected["weights"])
    confirmation_result = evaluate_subset(
        "held-out-two-pair-confirmation", confirmation_observations, selected["weights"]
    )
    external_result = evaluate_subset("external-stability-campaign", external, selected["weights"])
    decision = gate_decision(screen_result, confirmation_result, external_result)

    projection = decode_projection(
        development,
        selected["weights"],
        c_cfg,
        calibration_pairs=set(split["fit_and_profile_selection_pair_indices"]),
    )
    oracle_report: dict[str, Any]
    if oracle is None:
        oracle_report = {
            "status": "not-evaluated-phase-legitimacy-gate-failed",
            "phase_legitimacy": phase_report,
        }
    else:
        oracle_screen = evaluate_subset(
            "held-out-two-pair-screen", screen_observations, oracle["weights"]
        )
        oracle_confirmation = evaluate_subset(
            "held-out-two-pair-confirmation", confirmation_observations, oracle["weights"]
        )
        oracle_report = {
            "status": "descriptive-fixed-route-oracle-only",
            "phase_legitimacy": phase_report,
            "profile": serialize_profile(oracle, cfg),
            "fit_summary": oracle["fit_summary"],
            "fit_comparison": oracle["fit_comparison"],
            "screen": oracle_screen,
            "confirmation": oracle_confirmation,
        }

    script_sha = sha256_file(Path(__file__).resolve())
    analysis = {
        "schema": SCHEMA_VERSION,
        "status": "complete-offline-replay",
        "preregistration_sha256": prereg_sha,
        "analysis_code_sha256": script_sha,
        "inventory_sha256": sha256_file(args.output_dir / "inventory.json"),
        "profile_sha256": profile_sha,
        "selected_profile": {
            "profile_id": selected["profile_id"],
            "kind": selected["kind"],
            "regularization": selected["regularization"],
            "boost_cap_db": selected["boost_cap_db"],
            "maximum_inversion_gain_db": profile_document["maximum_inversion_gain_db"],
            "p99_inversion_gain_db": profile_document["p99_inversion_gain_db"],
        },
        "fit_candidate_table": candidate_table,
        "held_out_screen": screen_result,
        "held_out_confirmation": confirmation_result,
        "external_stability": external_result,
        "complex_inverse_oracle": oracle_report,
        "block_and_byte_projection": projection,
        "decision": decision,
        "unsupported_evidence": {
            "thd": "not measured by retained corpus",
            "intermodulation": "not measured by retained corpus",
            "speaker_or_os_protection": "not observable in flat-only retained captures",
            "calibrated_acoustic_level_or_exposure": "not measured",
            "three_offset_transfer_stability": "not present; all runs use one nominal pose",
            "ota_pre_equalized_goodput": "not measured",
        },
        "claim_boundary": (
            "All candidate SINR/GMI/block figures are linear counterfactual replay "
            "estimates. Only flat incumbent block outcomes are retained OTA observations."
        ),
    }
    write_json(args.output_dir / "analysis.json", analysis)
    acquisition = acquisition_manifest(prereg_sha, profile_path, profile_sha, decision)
    write_json(args.output_dir / "ota-acquisition.manifest.json", acquisition)
    summary = {
        "decision": decision["decision"],
        "profile_id": selected["profile_id"],
        "screen_p10_sinr_gain_db": screen_result["candidate_vs_flat"][
            "p10_effective_sinr_gain_db"
        ],
        "confirmation_p10_sinr_gain_db": confirmation_result["candidate_vs_flat"][
            "p10_effective_sinr_gain_db"
        ],
        "external_p10_sinr_gain_db": external_result["candidate_vs_flat"][
            "p10_effective_sinr_gain_db"
        ],
        "projection_status": projection["candidate_projection_status"],
        "output": str(args.output_dir / "analysis.json"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
