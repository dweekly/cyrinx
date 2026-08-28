import CyrinxChatApp
import CyrinxChatKit
import Foundation

/// A `ChatTransportClient` that never emits anything and accepts every
/// command as a no-op -- exists only so `#Preview` blocks across this
/// directory can construct a `ChatModel` without a real simulated pair.
/// Not used by the shipping app (see `ChatAppSession`, which always uses
/// the real `SimulatedChatTransportClient`).
struct PreviewTransport: ChatTransportClient {
    let events: AsyncStream<ChatEvent> = AsyncStream { _ in }
    func start() async throws {}
    func stop() async {}
    func connect(toPeer idHex: String) async throws {}
    func disconnect() async {}
    func send(body: String) async throws -> String { Data(repeating: 0, count: 16).hexString }
    func cancelSend(messageIdHex: String) async {}
}
