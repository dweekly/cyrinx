import Cyrinx
import Foundation

@main
struct LoopbackExample {
    static func main() throws {
        let desktop = try CyrinxSession(config: Config(role: .master))
        let phone = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(desktop, phone)
        try desktop.start()
        try phone.start()

        let outbound = Data("hello from desktop".utf8)
        try desktop.send(
            outbound,
            streamID: 100,
            qos: .reliable,
            priority: .high,
            flags: [.fin]
        )

        guard let inbound = try phone.receive(timeoutMS: 500) else {
            throw NSError(domain: "ExampleLoopback", code: 1, userInfo: [NSLocalizedDescriptionKey: "Timed out"])
        }

        let text = String(decoding: inbound.data, as: UTF8.self)
        print("received='\(text)' stream=\(inbound.streamID) priority=\(inbound.priority.rawValue) fin=\(inbound.flags.contains(.fin))")

        let metrics = desktop.metrics
        print("tx_frames=\(metrics.txFrames) retries=\(metrics.txRetries) goodput_bps=\(metrics.goodputBps)")
    }
}
