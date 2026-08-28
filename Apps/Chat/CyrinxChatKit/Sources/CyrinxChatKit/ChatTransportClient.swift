import Foundation

/// Platform-neutral chat transport contract. Apps/Chat/CONTRACT.md §1.8.
///
/// Consumed by UI models (never touched directly by UI code -- see
/// Apps/Chat/README.md); implemented here by `SimulatedChatTransportClient`
/// and, in C3-31, by a live Cyrinx 3 SDK adapter behind the same contract.
public protocol ChatTransportClient {
    func start() async throws
    func stop() async

    /// Bounded, buffering newest 512 (drop-oldest); see the `eventSeq`
    /// gap-detection contract and bounded-buffer policy documented on
    /// `ChatEvent` and implemented by `ChatEventEmitter`
    /// (CONTRACT.md §1.7): a consumer detects drops by watching for
    /// `eventSeq` gaps (`gap = currentEventSeq - previousEventSeq - 1`);
    /// survivors keep their true, originally assigned `eventSeq` and
    /// relative order, so a gap is always detectable even though it is
    /// never actively resynced in this sample.
    var events: AsyncStream<ChatEvent> { get }

    func connect(toPeer idHex: String) async throws
    func disconnect() async

    /// Returns on queue acceptance, NOT delivery -- see
    /// `ChatMessageDisplayStatus`'s honest-delivery caveat (CONTRACT.md
    /// §1.5). The returned string is the new message's `messageIdHex`.
    func send(body: String) async throws -> String
    func cancelSend(messageIdHex: String) async
}
