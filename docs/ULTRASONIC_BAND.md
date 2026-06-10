# Ultrasonic-Band (Inaudible) Bulk PHY — Effort Plan

Fresh as of 2026-06-09. Status: in progress (tracking PR opens with this commit).

Goal: rerun the bulk-PHY methodology of [ACOUSTIC_BULK_PHY.md](ACOUSTIC_BULK_PHY.md)
restricted to the inaudible band (>= ~18.5 kHz), maximizing measured goodput in
both directions. 96 kHz sampling is allowed where it helps (it moves the
anti-alias filters far above 24 kHz and opens >24 kHz if transducers permit).

Plan (strikethrough as completed):
1. Verify 96 kHz capture/playback on both devices (Pixel AudioRecord/AudioTrack
   UNPROCESSED @96k; Mac sounddevice @96k).
2. Measure the ultrasonic channel at 96 kHz both directions: per-bin SNR
   18-40 kHz, noise floors, audible-band leakage of a shaped ultrasonic probe.
3. Parameterize the modem for sample rate + band; move chirp/sync into the
   ultrasonic band; add per-symbol edge windowing to keep audible splatter down.
4. OTA MCS ladder in-band; maximize goodput each direction.
5. Honest ceiling analysis: the 48 kHz survey measured Pixel->Mac SNR of only
   10 dB (18-21 kHz) and 4.6 dB (21-24 kHz) at full volume, which caps that
   direction near ~14 kbps Shannon in a 5 kHz band; expectation is asymmetric
   results. Document measured reality either way.

Expected hardware constraints (from prior measurement): Mac speakers fine to
23-24 kHz; Pixel speaker output above 18 kHz is weak; >24 kHz both transducer
chains are unmeasured at 96k and may roll off steeply.
