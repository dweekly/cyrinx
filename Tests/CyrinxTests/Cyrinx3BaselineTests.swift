import Testing

@testable import Cyrinx

// Define custom tags for Swift Testing.
extension Tag {
    @Tag static var criticalContract: Self
    @Tag static var conformance: Self
    @Tag static var slow: Self
}

@Suite("Cyrinx 3.0 Baseline Tests")
struct Cyrinx3BaselineTests {

    @Test("Verify API version consistency", .tags(.criticalContract, .conformance))
    func testVersion() {
        #expect(Cyrinx.version == "2.0.0")
    }

    @Test("Verify status name helper", .tags(.conformance))
    func testStatusName() {
        #expect(Cyrinx.statusName(for: -4) == "CYRINX_ERR_TIMEOUT")
    }

    @Test("Slow baseline simulation run", .tags(.slow))
    func testSlowDummy() async throws {
        try await Task.sleep(nanoseconds: 10_000_000)  // 10ms
        #expect(Bool(true))
    }
}
