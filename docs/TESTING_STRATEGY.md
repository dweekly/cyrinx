# Cyrinx Testing Strategy & Continuous Integration

This document outlines the testing architecture, framework co-existence model, tagging taxonomy, local verification instructions, and continuous integration pipeline for the Cyrinx acoustic communication library.

---

## 1. Testing Philosophy & Framework Co-existence

Cyrinx employs a hybrid test suite consisting of two main testing frameworks: **XCTest** and **Swift Testing**. Rather than forcing a legacy migration or splitting libraries, they co-exist within the same test target (`CyrinxTests`).

### Framework Roles

1. **XCTest**:
   - Used for legacy unit tests, XCUITest, snapshot/visual tools requiring UIKit/AppKit contexts, and `XCTMetric`-based performance tests.
   - Retained for all tests written prior to the 3.0 baseline, minimizing implementation churn.

2. **Swift Testing**:
   - Mandated for new unit, contract, state-machine, and async integration tests.
   - Leverage modern Swift features such as macro-based assertions (`#expect`, `#require`), parameterized testing, suites (`@Suite`), and tags.

### Isolation Guidelines
- **No mixed files**: A single source file must not import/use both XCTest and Swift Testing. Keep Swift Testing code isolated to dedicated files (e.g., [Cyrinx3BaselineTests.swift](file:///Users/dew/dev/cyrinx/Tests/CyrinxTests/Cyrinx3BaselineTests.swift)).
- **Location**: Test files must be placed in `Tests/CyrinxTests`.

---

## 2. Swift Testing Custom Tags

To categorize test suites and support selective test execution, Cyrinx defines three custom tags under `Tag` extensions in Swift Testing:

- **`criticalContract`**:
  - *Definition*: Verifies strict API contract correctness, ABI stability, and serialization layout boundaries.
  - *Usage*: Apply to tests validating byte sizes, alignments of C structs, error mappings, and version invariants.

- **`conformance`**:
  - *Definition*: Validates standard protocol and physical layer conformance.
  - *Usage*: Apply to tests verifying DSP outputs, codecs (Morse/Nibble), channel calibration metrics, and protocol framing.

- **`slow`**:
  - *Definition*: Identifies tests that require substantial execution time (e.g., >100ms) or run complex loopback simulations.
  - *Usage*: Apply to multi-packet loopback runs, dynamic simulations under heavy channel impairments, and cryptographic handshake tests.

### Declaration and Application

Custom tags are declared in `Tests/CyrinxTests/Cyrinx3BaselineTests.swift` as follows:

```swift
import Testing

extension Tag {
    @Tag static var criticalContract: Self
    @Tag static var conformance: Self
    @Tag static var slow: Self
}
```

Applying tags to a test case or suite:

```swift
@Test("Verify API version consistency", .tags(.criticalContract, .conformance))
func testVersion() {
    #expect(Cyrinx.version == "2.0.0")
}
```

---

## 3. Continuous Integration Pipeline

The CI workflow is defined in [.github/workflows/ci.yml](file:///Users/dew/dev/cyrinx/.github/workflows/ci.yml) and runs automatically on:
- Pushes to the `main` branch.
- Pull requests targeting `main`.

### Pipeline Environment
- **Runner**: `macos-14` (Apple Silicon runner supplying native ARM64 compilation and Accelerate framework availability).
- **Tooling Stack**: Swift PM, Xcode command-line tools, Android SDK, and JDK 17 (Zulu).

### Execution Steps
1. **Checkout**: Checks out repository source code.
2. **Java / Android Setup**: Sets up JDK 17 to build the HIL Android app.
3. **Dependency Installation**: Installs formatters and linters (`swiftlint`, `shellcheck`, `clang-format`, `swift-format`) via Homebrew.
4. **Format Verification**: Runs `./scripts/format-check.sh` to enforce code styling.
5. **Lint Analysis**: Runs `./scripts/lint.sh` to verify Swift and Shell scripts.
6. **Test Suites**: Invokes `swift test` to run all XCTest and Swift Testing suites concurrently.
7. **Android HIL Build**: Compiles the Android HIL application using `./gradlew assembleDebug` (if the Gradle configuration file is present).

---

## 4. Local Verification & Development Loop

To ensure compliance before pushing code or submitting a PR, developers must execute local gate checks.

- **Format Code**:
  ```bash
  ./scripts/format.sh
  ```
- **Check Formatting**:
  ```bash
  ./scripts/format-check.sh
  ```
- **Run Linters**:
  ```bash
  ./scripts/lint.sh
  ```
- **Execute All Tests**:
  ```bash
  swift test
  ```
- **Run Full Gate Check**:
  ```bash
  ./scripts/check.sh
  ```
- **Build Android HIL App**:
  ```bash
  cd Apps/HIL/android && ./gradlew assembleDebug
  ```
