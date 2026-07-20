# Repository Guidelines

## Project Structure & Module Organization

`Sources/Cyrinx` contains the Swift library, acoustic PHY, codecs, simulation, and Apple audio scaffolding. `Sources/CCyrinx` contains the C core and public headers under `Sources/CCyrinx/include/cyrinx`. `Tests/CyrinxTests` holds the Swift Testing and existing XCTest suites. Runnable Swift Package examples live in `Examples/*`, with target names declared in `Package.swift`. Hardware-in-the-loop assets live under `Apps/HIL`, including the SwiftUI macOS/iOS harness, generated Xcode project inputs, and the Android HIL app under `Apps/HIL/android`. Automation scripts are in `scripts/`; generated build and benchmark outputs belong in `.build/` or `artifacts/`.

## Build, Test, and Development Commands

- `swift build`: compile the Swift package, C target, and executable targets.
- `swift test`: run the deterministic Swift Testing and XCTest suites.
- `swift run cyrinx-example-loopback`: run the in-memory transport sample.
- `swift run cyrinx-sim-bench --profile quiet --out artifacts/bench/sim-quiet.json`: write benchmark JSON.
- `./scripts/format.sh`: apply Swift and C formatting.
- `./scripts/format-check.sh`: verify formatting without changing files.
- `./scripts/lint.sh`: run SwiftLint and ShellCheck.
- `./scripts/check.sh`: full local gate: format check, lint, and tests.
- `./scripts/hil-generate.sh`: regenerate the HIL Xcode project from `Apps/HIL/project.yml`.
- `cd Apps/HIL/android && ./gradlew assembleDebug`: build the Android HIL app.

## Coding Style & Naming Conventions

Use four-space indentation and keep Swift/C lines near the configured 110-column limit. Follow `.swift-format`, `.swiftlint.yml`, and `.clang-format`; do not hand-format around them. Swift types use `PascalCase`; methods, properties, and test names use `camelCase`. C API symbols use the existing `cyrinx_` / `CYRINX_` prefixes and public declarations belong in the matching `include/cyrinx` header. Keep identifiers ASCII.

## Testing Guidelines

Use Swift Testing (`@Test`, `#expect`, and `#require`) for new deterministic unit,
contract, state-machine, and async integration tests. Keep XCTest for XCUITest,
`XCTMetric` performance tests, snapshot tooling that requires it, and existing
tests until they are deliberately migrated. Do not mix the two frameworks in
one source file. Add tests near the behavior changed, especially for framing,
codecs, ARC state transitions, acoustic round trips, and C/Swift ABI boundaries.
Prefer deterministic in-memory or generated-waveform tests over
hardware-dependent checks. Run `swift test` for focused validation and
`./scripts/check.sh` before opening a PR.

## Commit & Pull Request Guidelines

Recent history uses short imperative commit subjects, for example `Add Android HIL app, ADB automation, and macOS Android HIL CLI`. Keep commits focused and avoid mixing generated artifacts with source edits unless the generation step is the change. PRs should include a concise summary, linked issue if applicable, test commands with results, and screenshots or diagnostic logs for HIL, Android, macOS, or UI-facing changes.

## Python Development & Environment Rules

- **Use a Virtual Environment (`venv`)**: You must always use a Python virtual environment (`.venv`) for all Python package installations and running any diagnostic/analysis scripts. Do not install Python packages globally or using `--break-system-packages` on the system Python. Always invoke scripts using the `.venv/bin/python` interpreter.

## Agent Tone & Reporting Guidelines

- **Empirical and Analytical Tone**: Maintain a strictly professional, scientific, and empirical tone. Avoid flowery, boasting, or overconfident language (e.g., words like "historic", "flawlessly", "perfectly", "100% correct").
- **Engineering-Focused Analysis**: Focus objectively on engineering data, physical measurements, and system constraints. Proactively document and detail what did not work or fell short of the ideal system behavior, providing precise postmortems with high granularity.

