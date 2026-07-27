import XCTest

/// Shared launch/wait plumbing for this target's page objects.
///
/// Every flow drives the built app entirely through CONTRACT.md §5's
/// launch arguments (`chat.scenario`/`chat.seed`) plus this sample's own
/// app-only `chat.attachTo` (see `Shared/ChatAppOnlyLaunchArgs.swift`) --
/// no audio, no two physical devices, no live SDK. Per the design brief's
/// "Rules for all agents": no wall-clock sleeps; every bounded wait here
/// is asserted fail-closed (`waitForExistence`'s own `timeout:` is XCTest's
/// standard polling primitive, not a fixed `sleep`, and every call site
/// below asserts its boolean result rather than ignoring a timeout).
enum ChatUITestSupport {
    /// Generous enough for CI/CI-adjacent hardware to build UI state
    /// (SwiftUI view updates, the app's own real-time virtual-clock pacing
    /// -- `ChatAppSession`'s 20 ms ticks -- reaching a scenario's
    /// post-connect steps) without being so long a genuine regression hangs
    /// the suite. `slowLink`'s own scripted delivery (2.5 real seconds,
    /// `ChatAppSession`'s 1:1 real-time pacing) is the single longest
    /// interval any flow below waits for, so this is comfortably above it.
    static let defaultTimeout: TimeInterval = 10

    /// Launches the app with `chat.scenario`/`chat.seed` (CONTRACT.md §5)
    /// and this sample's own `chat.attachTo`, and returns it running.
    /// `chat.simulated` is intentionally omitted -- always `true` through
    /// C3-28-C3-30 regardless (CONTRACT.md §5), so there is nothing this
    /// launcher needs to pass for it. `@MainActor`: recent XCTest SDKs
    /// isolate `XCUIApplication`'s initializer, `launchArguments`, and
    /// `launch()` to the main actor (same rationale as `assertExists`
    /// below); every call site is already an `@MainActor` `XCTestCase`.
    @MainActor
    static func launch(scenario: String, seed: UInt64 = 1, attachToB: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = [
            "-chat.scenario", scenario,
            "-chat.seed", String(seed),
            "-chat.attachTo", attachToB ? "b" : "a",
        ]
        app.launch()
        return app
    }
}

extension XCUIElement {
    /// Waits up to `timeout` for this element to exist, then `XCTAssert`s
    /// it did -- a single call site that fails the test (with `file`/`line`
    /// pointing at the caller) rather than letting a missing element
    /// surface as a confusing later failure. `@MainActor`: recent XCTest
    /// SDKs isolate `XCUIElement`'s querying members to the main actor.
    @discardableResult
    @MainActor
    func assertExists(
        timeout: TimeInterval = ChatUITestSupport.defaultTimeout,
        _ message: String = "",
        file: StaticString = #filePath,
        line: UInt = #line
    ) -> XCUIElement {
        XCTAssertTrue(waitForExistence(timeout: timeout), message, file: file, line: line)
        return self
    }
}
