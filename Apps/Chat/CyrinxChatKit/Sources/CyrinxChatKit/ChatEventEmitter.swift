/// Bounded event stream backing `ChatTransportClient.events`. Apps/Chat/
/// CONTRACT.md §1.7: "The event stream is a bounded buffer of 512 events,
/// drop-oldest ... Events that ARE delivered are delivered in original
/// relative order, each with its true, originally assigned `eventSeq` --
/// dropping never renumbers or reorders survivors." Backed by
/// `AsyncStream(bufferingPolicy: .bufferingNewest(512))`, the exact Swift
/// mechanism CONTRACT.md §1.7 pins.
///
/// `eventSeq` is assigned by an ever-incrementing counter *before* the
/// `AsyncStream` yield, so a dropped event still consumes a sequence
/// number -- this is what makes gaps detectable by a consumer computing
/// `gap = currentEventSeq - previousEventSeq - 1` (verified directly by
/// `ChatEventEmitterTests`'s buffer-overflow test, independent of any
/// scenario).
///
/// Owned exclusively by one `SimulatedChatTransportClient` instance and
/// only ever touched from that client's own (single-threaded, per
/// `VirtualClock`'s documentation) call sequence -- never shared or raced
/// across instances.
///
/// **Concurrency note (C3-28 review):** deliberately does NOT conform to
/// `Sendable`, not even `@unchecked` -- `nextEventSeq` is genuinely
/// unsynchronized mutable state, matching `SimulatedChatTransportClient`'s
/// and `VirtualClock`'s own non-`Sendable` rationale (see their doc
/// comments). Verified empirically: removing the prior `@unchecked
/// Sendable` conformance compiles clean under this package's default Swift
/// 6 language mode with zero new warnings or errors, and every `swift
/// test` case still passes -- nothing in this package or its tests
/// actually crosses an actor-isolation boundary with an instance of this
/// class.
final class ChatEventEmitter {
    /// The bound pinned by CONTRACT.md §1.7.
    static let bufferCapacity = 512

    let stream: AsyncStream<ChatEvent>
    private let continuation: AsyncStream<ChatEvent>.Continuation
    private var nextEventSeq: UInt64 = 0

    init() {
        var capturedContinuation: AsyncStream<ChatEvent>.Continuation?
        self.stream = AsyncStream(bufferingPolicy: .bufferingNewest(Self.bufferCapacity)) { continuation in
            capturedContinuation = continuation
        }
        // The closure passed to AsyncStream's initializer runs synchronously,
        // so `capturedContinuation` is always set by this point.
        self.continuation = capturedContinuation!
    }

    @discardableResult
    func emit(_ kind: ChatEvent.Kind) -> ChatEvent {
        let event = ChatEvent(eventSeq: nextEventSeq, kind: kind)
        nextEventSeq += 1
        continuation.yield(event)
        return event
    }

    func finish() {
        continuation.finish()
    }
}
