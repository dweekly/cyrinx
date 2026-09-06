#!/usr/bin/env python3
"""Conservative link geometries for the garage baseline map (plan stage G0).

The garage research decodes host-side through the C bulk codec, which takes a
``cyrinx_bulk_config`` directly (``clib.make_cfg`` -> ``cyrinx_bulk_*``). It
does not resolve profiles through ``cyrinx_profiles.c``, so nothing here adds a
registry row: these are research configurations, and a registry row is a
promotion-time artifact for a geometry that has already won a comparison.

**Geometries are derived per directed link from endpoint capability, not from a
role.** The occupied band is a property of the transmitting endpoint's speaker
and the receiving endpoint's microphone, so it is computed as the intersection
of the two. Naming a geometry "uplink" or "downlink" would bake in a topology --
laptop strong, phone weak -- that is wrong for laptop-to-laptop, wrong for
phone-to-phone, and wrong in an unpredictable direction for a high-end phone
talking to a low-end one. C3-18's merge gate is that two *symmetric* peers
converge on complementary roles, elected at runtime; capability travels in the
beacon's capability hash and is measured by C3-20b self-characterization, not
assumed. This module follows that contract.

An endpoint whose capability has not been measured gets ``UNKNOWN_ENDPOINT``,
which is the narrowest band any device here has measured. Unknown therefore
degrades to conservative rather than to optimistic.

Every constant carries its source. Two are session parameters rather than frozen
values -- ``amp`` and the chosen input gain -- because the plan fixes the
playback level per device during setup and holds it for a batch.

Sources referenced throughout:
  registry      Sources/CCyrinx/cyrinx_profiles.c, the five registered rows
  NF-<n>        docs/NEGATIVE_FINDINGS.md, numbered entry
  NOTES         scratch/hw20k/NOTES.md, section noted per constant
  IOS_HIL       docs/IOS_HIL.md
"""

from dataclasses import dataclass, replace

# --- Constants shared by every conservative geometry ------------------------

SAMPLE_RATE_HZ = 48000
"""registry: all five registered rows are 48 kHz."""

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
(about 1719 samples at 48 kHz)."""

PRACTICAL_GUARD_BUDGET_MS = 16.0
"""The guard budget stage 1G compares measured late energy against, equal to
CYCLIC_PREFIX_SAMPLES at SAMPLE_RATE_HZ. Declared here, before the batch, so the
gate cannot be argued after the fact. Late energy substantially beyond this moves
a position to stage 8 screening; it does not license a claim that the position is
unrecoverable (NF-1, NF-13)."""

PILOT_EVERY = 8
"""registry row 1. The densest pilot spacing in the registry, so it gives the
channel estimator the most to work with. NF-6 records that pilot-local
reliability weighting is what the receiver leans on, so thinning pilots is a
later experiment, not a starting point."""

BITS_PER_BIN = 2
"""QPSK. NF-12 records that mixed maps containing 1-bit (BPSK) bins are broken
even in clean digital loopback (open bug, issue #4), worked around with a QPSK
floor, so 2 is the lowest order that may be used here."""

CODE_RATE = "1/2"
"""The most robust rate the C codec exposes. registry rows use 3/4, 2/3, and
5/6; there is no rate-1/2 row, which is why the conservative arm of the stage 1
map had nothing to run before G0."""

SYMBOL_COUNT = 64
"""registry rows 1, 2, 3, and 5. Row 4 uses 96."""

CLIP_SIGMA = 3.3
"""registry: identical across all five rows."""

CHIRP_F0_HZ = 2000.0
CHIRP_F1_HZ = 16000.0
"""registry: identical across all five rows. NF-13 recorded the chirp locking
cleanly (matched-filter peak/mean 104) in a geometry that then decoded nothing,
so sync acquisition and payload recovery must be reported separately."""

COHERENT_CEILING_HZ = 18000.0
"""No coherent geometry may reach above this on any endpoint measured so far.
NF-9: both the Pixel 7a and the iPhone 17 Pro Max speakers are phase-incoherent
above ~18 kHz -- they radiate ultrasonic power, but single-tone phase jitter runs
to 10-31 rad, so coherent OFDM yields EVM ~1.0. Two models do not establish a
universal limit, which is why this is applied to phone emission rather than to
every endpoint."""


@dataclass(frozen=True)
class EndpointCapability:
    """What one endpoint can usefully emit and capture, and how we know.

    ``emit_hz`` and ``capture_hz`` are measured properties of a specific device
    in a specific configuration, not a device class: the Moto G's usable band
    depends on whether Dolby DAX is disabled (NOTES A5). C3-20b replaces this
    table with per-endpoint self-characterization at association time; until
    then these are the bench measurements on record.
    """

    label: str
    emit_hz: tuple
    capture_hz: tuple
    provenance: str

    def __post_init__(self):
        for name, band in (("emit_hz", self.emit_hz), ("capture_hz", self.capture_hz)):
            if len(band) != 2 or not 0 < band[0] < band[1]:
                raise ValueError(f"{self.label}: {name} must be (lo, hi), got {band!r}")


MACBOOK_PRO_M4 = EndpointCapability(
    label="macbook-pro-m4",
    emit_hz=(300.0, 23000.0),
    capture_hz=(300.0, 23000.0),
    provenance="NOTES: M->A usable band ~0.3-23 kHz, flat; registry rows occupy "
    "1100-23000 Hz on this transmitter.",
)

PIXEL_7A = EndpointCapability(
    label="pixel-7a",
    emit_hz=(300.0, 17000.0),
    capture_hz=(300.0, 23000.0),
    provenance="NOTES: A->M usable band ~0.3-17 kHz; the Pixel speaker rolls off "
    "above 17 kHz and NF-9 puts it beyond coherent use above ~18 kHz.",
)

IPHONE_17_PRO_MAX = EndpointCapability(
    label="iphone-17-pro-max",
    emit_hz=(600.0, 11000.0),
    capture_hz=(300.0, 23000.0),
    provenance="IOS_HIL: iPhone->Mac ran 0.6-11 kHz; the speaker rolls off ~18 dB "
    "by 10-14 kHz. Its microphone carried Mac->iPhone across 1.1-23 kHz.",
)

MOTO_G_2026 = EndpointCapability(
    label="moto-g-2026",
    emit_hz=(600.0, 14000.0),
    capture_hz=(300.0, 23000.0),
    provenance="NOTES A5: speaker cliffs at 14 kHz (-40 dB by 14-17 kHz), band-fit "
    "to 0.6-14 kHz recovered the link. Requires Dolby DAX disabled.",
)

UNKNOWN_ENDPOINT = EndpointCapability(
    label="unknown",
    emit_hz=(600.0, 11000.0),
    capture_hz=(600.0, 11000.0),
    provenance="The narrowest emission measured on any endpoint here "
    "(IPHONE_17_PRO_MAX). An uncharacterized endpoint degrades to the most "
    "conservative band rather than an optimistic one.",
)

MEASURED_ENDPOINTS = {
    e.label: e
    for e in (MACBOOK_PRO_M4, PIXEL_7A, IPHONE_17_PRO_MAX, MOTO_G_2026)
}

PHONE_ENDPOINTS = frozenset({"pixel-7a", "iphone-17-pro-max", "moto-g-2026"})
"""Endpoints NF-9's phase-incoherence finding was measured on, plus devices of
the same class. COHERENT_CEILING_HZ is applied to emission from these."""


@dataclass(frozen=True)
class Geometry:
    """One frozen research configuration for a single directed link."""

    label: str
    transmitter: str
    receiver: str
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


def conservative_link(transmitter, receiver, amp):
    """The conservative geometry for the directed link ``transmitter`` -> ``receiver``.

    The band is the intersection of what the transmitter can emit and what the
    receiver can capture, capped at COHERENT_CEILING_HZ when the transmitter is
    a phone. Both arguments are ``EndpointCapability``; pass ``UNKNOWN_ENDPOINT``
    for an endpoint that has not been characterized.

    Symmetry is a property of the pair, not of the code: two laptops get the same
    wide band in both directions, two phones get the same narrow band in both,
    and a mixed pair gets an asymmetry that was computed rather than assumed.
    """
    lo = max(transmitter.emit_hz[0], receiver.capture_hz[0])
    hi = min(transmitter.emit_hz[1], receiver.capture_hz[1])
    if transmitter.label in PHONE_ENDPOINTS:
        hi = min(hi, COHERENT_CEILING_HZ)
    if lo >= hi:
        raise ValueError(
            f"{transmitter.label} -> {receiver.label} has no usable band: "
            f"transmitter emits {transmitter.emit_hz}, receiver captures "
            f"{receiver.capture_hz}"
        )
    return Geometry(
        label=f"garage-conservative-{transmitter.label}-to-{receiver.label}-v1",
        transmitter=transmitter.label,
        receiver=receiver.label,
        f_lo_hz=lo,
        f_hi_hz=hi,
        amp=amp,
    )


def conservative_pair(a, b, amp_a, amp_b):
    """Both directed geometries for a pair, as ``(a_to_b, b_to_a)``.

    Amplitudes are per transmitting endpoint because they are session parameters
    fixed per device at setup, and because phone speakers run hotter than laptop
    speakers to reach the same received level (NOTES A5).
    """
    return conservative_link(a, b, amp_a), conservative_link(b, a, amp_b)


# --- Reference playback amplitudes ------------------------------------------
#
# Starting points for setup, not frozen values. The plan fixes one comfortable
# level per device during setup, records the visible setting and any API
# readback, and holds it for the batch.

REFERENCE_AMP_LAPTOP = 0.5
"""registry row 1. Rows 2 and 5 use 0.13 and rows 3 and 4 use 0.18, all tuned for
near-field cells where the laptop speaker is centimetres from the phone."""

REFERENCE_AMP_PHONE = 0.7
"""scratch/hw20k/uplink_qpsk.py, the A5 spike. Phone speakers are the weaker
emitter on every pair measured so far, so they run hotter."""
