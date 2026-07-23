import CyrinxChatKit
import Foundation
import Testing

@testable import CyrinxChatApp

/// Format and determinism tests for `ChatModelTraceRecorder` -- the design
/// brief's pinned model-trace schema: canonical field order `eventSeq,
/// connection, budget, peers, messages, banner, gap`, exact `": "`/`", "`
/// spacing, and byte-identical output for the same `(scenario, seed)` --
/// the same property `chat-model-trace-gen`'s own determinism gate depends
/// on. No sleeps anywhere in this file.
@Suite("ChatModelTraceRecorder: schema and determinism")
struct ChatModelTraceRecorderTests {
    @Test("one record matches the pinned canonical field order and spacing exactly")
    func canonicalLineFormat() {
        let recorder = ChatModelTraceRecorder()
        recorder.record(
            eventSeq: 2,
            connectionWire: "connected",
            budgetClassWire: "text",
            peerIdHexes: ["a1b2c3d4"],
            messageEntries: [(idHex: "deadbeef", direction: "outgoing", status: "delivered")],
            banner: nil,
            gap: false
        )

        let expected = """
            {"eventSeq": 2, "connection": "connected", "budget": "text", "peers": ["a1b2c3d4"], \
            "messages": [{"idHex": "deadbeef", "direction": "outgoing", "status": "delivered"}], \
            "banner": null, "gap": false}
            """
        #expect(recorder.lines == [expected])
    }

    @Test("a non-null banner is quoted, empty peers/messages arrays render as [], gap true renders as true")
    func emptyCollectionsAndNonNullBanner() {
        let recorder = ChatModelTraceRecorder()
        recorder.record(
            eventSeq: 0,
            connectionWire: "disconnected",
            budgetClassWire: "controlOnly",
            peerIdHexes: [],
            messageEntries: [],
            banner: "Disconnected.",
            gap: true
        )

        let expected = """
            {"eventSeq": 0, "connection": "disconnected", "budget": "controlOnly", "peers": [], \
            "messages": [], "banner": "Disconnected.", "gap": true}
            """
        #expect(recorder.lines == [expected])
    }

    @Test("multiple peers and messages preserve caller-given order, comma-space separated")
    func multipleEntriesPreserveOrder() {
        let recorder = ChatModelTraceRecorder()
        recorder.record(
            eventSeq: 5,
            connectionWire: "connected",
            budgetClassWire: "bulk",
            peerIdHexes: ["aaaa", "bbbb"],
            messageEntries: [
                (idHex: "1111", direction: "outgoing", status: "queued"),
                (idHex: "2222", direction: "incoming", status: "delivered"),
            ],
            banner: nil,
            gap: false
        )

        let expected = """
            {"eventSeq": 5, "connection": "connected", "budget": "bulk", "peers": ["aaaa", "bbbb"], \
            "messages": [{"idHex": "1111", "direction": "outgoing", "status": "queued"}, \
            {"idHex": "2222", "direction": "incoming", "status": "delivered"}], "banner": null, "gap": false}
            """
        #expect(recorder.lines == [expected])
    }

    @Test("joinedText() joins lines with a single newline and ends with a trailing newline")
    func joinedTextFormat() {
        let recorder = ChatModelTraceRecorder()
        recorder.record(
            eventSeq: 0, connectionWire: "connected", budgetClassWire: "text",
            peerIdHexes: [], messageEntries: [], banner: nil, gap: false
        )
        recorder.record(
            eventSeq: 1, connectionWire: "degraded", budgetClassWire: "controlOnly",
            peerIdHexes: [], messageEntries: [], banner: "Link degraded — move devices closer", gap: false
        )

        let text = recorder.joinedText()
        #expect(text == recorder.lines[0] + "\n" + recorder.lines[1] + "\n")
        #expect(text.hasSuffix("\n"))
        #expect(!text.hasSuffix("\n\n"))
    }

    @Test("a full happyPair run's recorded trace is byte-identical across two runs of the same seed")
    @MainActor
    func fullScenarioTraceIsDeterministic() async throws {
        func runOnce() async throws -> String {
            let recorder = ChatModelTraceRecorder()
            try await ChatModelScenarioDriver.run(scenario: .happyPair, seed: 1) { transport in
                let model = ChatModel(transport: transport, now: { 0 })
                model.traceRecorder = recorder
                return model
            }
            return recorder.joinedText()
        }

        let first = try await runOnce()
        let second = try await runOnce()
        #expect(first == second)
        #expect(!first.isEmpty)
    }
}
