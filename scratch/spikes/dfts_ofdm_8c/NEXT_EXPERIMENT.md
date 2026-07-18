# Minimum next experiment after the Spike 8c stop

The frozen Spike 8c screen does not permit OTA. Its primary q99.9 crest-factor
reduction was 2.7775 dB, below the 3.0 dB gate. More importantly for the
physical hypothesis, the shared 3.3-sigma limiter reduced the held-out
post-limiter crest-factor difference to about 0.011 dB and the equal-peak RMS
advantage to about 0.009 dB. The candidate therefore did not demonstrate a
materially different waveform at the simulated speaker input used by the
accepted transmitter.

Do not tune the DFT length, pilot arrangement, limiter, or threshold inside
this spike. The minimum defensible follow-up is conditional on Rank 5a first
finding a real nonlinear limitation:

1. Independently characterize the selected Mac output route with a
   preregistered, waveform-neutral level sweep. Retain input PCM and estimate
   repeatable AM/AM behavior, harmonics/intermodulation, spectral regrowth,
   compression, and protection-DSP state across repeated levels. Do not use
   payload recovery to select the model.
2. Freeze one measured input/output nonlinearity model and a held-out set of
   complete physical cells. Replay the already frozen conventional and
   localized-DFT mappings at equal peak through that model, with both the
   production 3.3-sigma limiter and a separately labelled no-limiter
   diagnostic. The primary metric should be payload-independent GMI or actual
   production-code recovery, not raw crest factor alone.
3. Reopen an OTA candidate screen only if the held-out measured-nonlinearity
   replay predicts at least a 5% lower-tail scheduled-goodput gain or an
   equivalent material robustness gain at noninferior spectral behavior.

If Rank 5a does not find repeatable output compression/protection that the
production OFDM waveform triggers, stop this localized DFT-spread branch. The
current synthetic screen is not evidence that a different pilot layout or a
post-hoc 2.75 dB threshold should be tried.

