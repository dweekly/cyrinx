# Contributing to Cyrinx

Thanks for your interest. Cyrinx is a research prototype exploring
data-over-sound for close-range desktop-to-phone links; see
[README.md](README.md) for scope and measured results, and
[ROADMAP.md](ROADMAP.md) for the active post-2.0 engineering priorities.

## Ground rules

- **Measured, not assumed.** This project values *measured* over-the-air
  goodput over simulation or capacity calculations. If you claim a result,
  include how it was measured (hardware, geometry, byte-verified goodput).
  See [docs/NEGATIVE_FINDINGS.md](docs/NEGATIVE_FINDINGS.md) for hard-won dead
  ends — please don't make us rediscover them.
- **Ship the spike.** Standalone validators (e.g. `scratch/fft/`) stay in the
  repo as reproducible diagnostics rather than being deleted after they pay off.
- **Cross-implementation parity.** The DSP has Python, Swift, Kotlin, and (in
  progress) portable-C implementations. Changes to one should be checked against
  the golden vectors so the others stay in agreement.

## Development

```bash
swift test            # Swift + C unit tests
./scripts/check.sh    # format-check + lint + tests (the full gate)
```

- Format before committing: `./scripts/format.sh`.
- Keep `swift test` green. New behavior gets a test; new measured results get a
  note in the relevant `docs/` file with a "fresh as of" date.
- Don't hardcode undocumented constants — cite the source (datasheet, SDK
  header, or measurement) in a comment.
- **swift-format is pinned to `603.0.0`** (see `scripts/swift-format-version.sh`,
  which `format.sh`/`format-check.sh` source and enforce). Install it with
  `brew install swift-format`. If your local version drifts, the gate fails
  loudly instead of silently reformatting to different rules — that drift is
  exactly what caused issue #25 (~900 stale violations on `main`). Note that
  Homebrew's `swift-format` and the Xcode-toolchain one (`xcrun --find
  swift-format`) are different binaries and can disagree; make sure the one
  on `PATH` is the pinned one (`which swift-format`).

## Pull requests

- Branch from `main`; open a PR early as a tracking PR.
- Use merge commits (no squash) to preserve commit history.
- Reference the relevant issue / `docs/PUBLICATION.md` stage.

## License

By contributing, you agree that your contributions are licensed under the
Apache License 2.0 (see [LICENSE](LICENSE)).
