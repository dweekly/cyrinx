/// Drives one of the six pinned scenario scripts (CONTRACT.md §3)
/// end-to-end, performing the exact "driver:" actions each table specifies
/// at the exact virtual times pinned, and collects the resulting
/// JSON-lines trace records in true emission order across both clients.
///
/// This is the mechanism behind `chat-trace-gen` (writes `happyPair` and
/// `peerLoss` traces for a given seed) and the scenario-determinism tests
/// (same scenario+seed twice -> byte-identical traces; different seed ->
/// different peer IDs).
public enum ChatScenarioRunner {
    /// Absolute virtual time (ms) at which the driver calls
    /// `A.connect(toPeer:)` in every one of the six scenario tables
    /// (CONTRACT.md §3.1-3.6).
    static let connectAtMs: Int64 = 100
    /// Absolute virtual time (ms) at which the driver calls `A.send(...)`,
    /// for the four scenarios that send at all (CONTRACT.md §3.1, 3.4-3.6).
    static let sendAtMs: Int64 = 300

    public static func runToCompletion(
        scenario: ChatScenario, seed: UInt64
    ) async throws -> [ChatTraceRecord] {
        let clock = VirtualClock()
        let (clientA, clientB) = SimulatedChatTransportClient.makePair(
            scenario: scenario, seed: seed, clock: clock
        )

        var records: [ChatTraceRecord] = []
        let sink: (ChatClientRole, Int64, ChatEvent) -> Void = { client, virtualTimeMs, event in
            records.append(
                ChatTraceRecord(
                    eventSeq: event.eventSeq, virtualTimeMs: virtualTimeMs, client: client, event: event.kind
                )
            )
        }
        clientA.traceSink = sink
        clientB.traceSink = sink

        // "0 | -> | -- | driver: A.start(), B.start(); simulator pairs A+B"
        // (identical opening row in every one of CONTRACT.md §3.1-3.6).
        try await clientA.start()
        try await clientB.start()

        // "100 | -> | -- | driver: A.connect(toPeer: peerB.idHex)".
        clock.advance(toMs: connectAtMs)
        try await clientA.connect(toPeer: clientB.localPeerId.hexString)

        if scenario.hasSendStep {
            // "300 | -> | -- | driver: A.send(body: ...) -> returns msg1/msgDup".
            clock.advance(toMs: sendAtMs)
            _ = try await clientA.send(body: scenario.canonicalSendBody)
        }

        clock.advance(toMs: scenario.scriptEndMs)

        // Detach the trace sink before this harness's own cleanup `stop()`
        // calls below. CONTRACT.md §3's tables are the exact, complete
        // pinned event list for each scenario -- no table has a `stop()`
        // driver row -- but CONTRACT.md §2's Lifecycle cancellation section
        // pins `stop()` itself to now emit real `ChatEvent`s whenever a
        // client isn't already disconnected when it's called (a
        // `connectionChanged(disconnected, reason: "stopped")`, plus any
        // nonterminal-send terminalization): correct, tested behavior of
        // `stop()` (see `SimulatedChatTransportClientTests`), but not part
        // of any of the six pinned scenario timelines or of what
        // `fixtures/traces/*.jsonl` pins. Every one of the six scenarios
        // reaches `scriptEndMs` still `.connected` except `peerLoss`
        // (already `.disconnected` autonomously by then), so without this
        // detach, `stop()`'s own cleanup event(s) would leak into the
        // recorded trace and both `ChatScenarioRunnerTests`'s pinned
        // per-scenario event counts and the golden-trace fixtures below
        // would go stale on every scenario but `peerLoss`.
        clientA.traceSink = nil
        clientB.traceSink = nil
        await clientA.stop()
        await clientB.stop()

        return records
    }
}
