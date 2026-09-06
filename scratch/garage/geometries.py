#!/usr/bin/env python3
"""Frozen conservative geometries for the garage baseline map (plan stage G0).

The garage research decodes host-side through the C bulk codec, which takes a
``cyrinx_bulk_config`` directly (``clib.make_cfg`` -> ``cyrinx_bulk_*``). It
does not resolve profiles through ``cyrinx_profiles.c``, so nothing here adds a
registry row: these are research configurations, and a registry row is a
promotion-time artifact for a geometry that has already won a comparison.

Every constant below carries its source. Two are session parameters rather than
frozen values -- ``amp`` and the chosen input gain -- because the plan fixes the
playback level per device during setup and holds it for a batch.

Sources referenced throughout:
  registry      Sources/CCyrinx/cyrinx_profiles.c, the five registered rows
  NF-<n>        docs/NEGATIVE_FINDINGS.md, numbered entry
  A5            scratch/hw20k/NOTES.md, "A5: third device pair" section
"""

from dataclasses import dataclass, replace

# --- Constants shared by both directions -----------------------------------

SAMPLE_RATE_HZ = 48000
"""registry: all five registered rows are 48 kHz. Keeping it fixes the sample
grid shared with the existing captures and the Schroeder readout."""

FFT_SIZE = 2048
"""registry: all five registered rows use NFFT 2048."""

CYCLIC_PREFIX_SAMPLES = 768
"""registry row 1 (the Cyrinx 1.x compatibility geometry) and the longest guard
in the registry: 16.0 ms at 48 kHz, which is 37.5% of the useful FFT interval
and 27.3% of the complete symbol.

This is also the plan's *declared practical guard budget* for stage 1G, chosen
for overhead rather than read off the registry as a physical limit. The
validator accepts any CP up to NFFT, so longer guards are expressible -- they
are simply not worth their airtime, and NF-13 measured NFFT 4096 with a 43 ms
guard recovering zero blocks in a geometry whose strong-tap spread was 35.8 ms
(about 1719 samples at 48 kHz). Widening the guard is not the answer that
geometry needed; see PRACTICAL_GUARD_BUDGET_MS."""

PRACTICAL_GUARD_BUDGET_MS = 16.0
"""The guard budget stage 1G compares measured late energy against, equal to
CYCLIC_PREFIX_SAMPLES at SAMPLE_RATE_HZ. Declared here, before the batch, so
the gate cannot be argued after the fact. Late energy substantially beyond this
moves a position to stage 8 screening; it does not license a claim that the
position is unrecoverable (NF-1, NF-13)."""

PILOT_EVERY = 8
"""registry row 1. The densest pilot spacing in the registry, so it gives the
channel estimator the most to work with -- the conservative choice. Rows 3 and 5
use 16 and row 4 uses 64; NF-6 records that pilot-local reliability weighting is
what the receiver leans on, so thinning pilots is a later experiment, not a
starting point."""

BITS_PER_BIN = 2
"""QPSK. NF-12 records that mixed maps containing 1-bit (BPSK) bins are broken
even in clean digital loopback (open bug, issue #4), worked around with a QPSK
floor, so 2 is the lowest order that may be used here."""

CODE_RATE = "1/2"
"""The most robust rate the C codec exposes. registry rows use 3/4, 2/3, and
5/6; there is no rate-1/2 row, which is exactly why the conservative arm of the
stage 1 map had nothing to run before G0."""

SYMBOL_COUNT = 64
"""registry rows 1, 2, 3, and 5. Row 4 uses 96."""

CLIP_SIGMA = 3.3
"""registry: identical across all five rows."""

CHIRP_F0_HZ = 2000.0
CHIRP_F1_HZ = 16000.0
"""registry: identical across all five rows. NF-13 recorded the chirp locking
cleanly (matched-filter peak/mean 104) in a geometry that then decoded nothing,
so sync acquisition and payload recovery must be reported separately."""

# --- Reference playback amplitudes -----------------------------------------
#
# These are starting points for setup, not frozen values. The plan fixes one
# comfortable level per device during setup, records the visible setting and any
# API readback, and holds it for the batch.

REFERENCE_AMP_DOWNLINK = 0.5
"""registry row 1. Rows 2 and 5 use 0.13 and rows 3 and 4 use 0.18, all tuned
for near-field cells where the laptop speaker is centimetres from the phone."""

REFERENCE_AMP_UPLINK = 0.7
"""scratch/hw20k/uplink_qpsk.py, the A5 uplink spike. Phone speakers are the
uplink bottleneck (A5: three independent device pairs), so the uplink runs
hotter than the downlink."""

# --- Bands ------------------------------------------------------------------

DOWNLINK_BAND_HZ = (1100.0, 23000.0)
"""registry: every registered row occupies 1100-23000 Hz, and the laptop speaker
carries it. Unchanged here so the conservative downlink differs from the
registered fast profiles in modulation, rate, and guard only."""

UPLINK_BAND_HZ = (600.0, 14000.0)
"""Narrower than the downlink because phone speakers are narrower, and chosen as
the intersection of what the phones on hand can actually emit:

  - A5 measured the Moto G 2026 speaker cliffing at 14 kHz (-40 dB by 14-17 kHz)
    and recovered its uplink by band-fitting to 0.6-14 kHz.
  - docs/IOS_HIL.md records the iPhone 17 Pro Max speaker rolling off by 10-14
    kHz, which is why its uplink band was narrowed to 0.6-11 kHz.
  - NF-9 records both the Pixel 7a and the iPhone speakers as phase-incoherent
    above ~18 kHz, so no coherent uplink may reach for the top of the downlink
    band on any of these devices.

A single band shared by both directions would put the uplink arm of the stage 1
map into a region already measured as unusable, which tests nothing. That is why
G0 freezes a directional pair rather than one geometry."""


@dataclass(frozen=True)
class Geometry:
    """One frozen research configuration, plus the label it is recorded under."""

    label: str
    direction: str  # "downlink" (laptop -> phone) or "uplink" (phone -> laptop)
    f_lo_hz: float
    f_hi_hz: float
    amp: float
    bits_per_bin: int = BITS_PER_BIN
    code_rate: str = CODE_RATE
    symbol_count: int = SYMBOL_COUNT
    fft_size: int = FFT_SIZE
    cyclic_prefix: int = CYCLIC_PREFIX_SAMPLES
    sample_rate_hz: int = SAMPLE_RATE_HZ
    pilot_every: int = PILOT_EVERY
    clip_sigma: float = CLIP_SIGMA

    @property
    def guard_ms(self):
        return 1000.0 * self.cyclic_prefix / self.sample_rate_hz

    def at_amplitude(self, amp):
        """Return this geometry at the level chosen for a session."""
        if not 0.0 < amp <= 1.0:
            raise ValueError(f"amplitude must be in (0, 1], got {amp!r}")
        return replace(self, amp=amp)

    def to_clib_cfg(self, clib):
        """Build the C ``cyrinx_bulk_config`` this geometry describes.

        ``clib`` is passed in rather than imported so a caller can hand in an
        explicitly selected build of the codec, which is what the plan requires
        of anything that produces a recorded result.
        """
        return clib.make_cfg(
            bits_per_bin=self.bits_per_bin,
            rate=self.code_rate,
            n_sym=self.symbol_count,
            f_lo=self.f_lo_hz,
            f_hi=self.f_hi_hz,
            nfft=self.fft_size,
            cp=self.cyclic_prefix,
            sr=self.sample_rate_hz,
            amp=self.amp,
            clip_sigma=self.clip_sigma,
            pilot_every=self.pilot_every,
        )


CONSERVATIVE_DOWNLINK = Geometry(
    label="garage-conservative-downlink-v1",
    direction="downlink",
    f_lo_hz=DOWNLINK_BAND_HZ[0],
    f_hi_hz=DOWNLINK_BAND_HZ[1],
    amp=REFERENCE_AMP_DOWNLINK,
)

CONSERVATIVE_UPLINK = Geometry(
    label="garage-conservative-uplink-v1",
    direction="uplink",
    f_lo_hz=UPLINK_BAND_HZ[0],
    f_hi_hz=UPLINK_BAND_HZ[1],
    amp=REFERENCE_AMP_UPLINK,
)

CONSERVATIVE = {
    "downlink": CONSERVATIVE_DOWNLINK,
    "uplink": CONSERVATIVE_UPLINK,
}


def conservative(direction):
    """The conservative geometry for ``direction``, by name."""
    try:
        return CONSERVATIVE[direction]
    except KeyError:
        raise ValueError(
            f"direction must be one of {sorted(CONSERVATIVE)}, got {direction!r}"
        ) from None
