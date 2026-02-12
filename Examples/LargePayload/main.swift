import Cyrinx
import Foundation

@main
struct LargePayloadExample {
    static func main() throws {
        let desktop = try CyrinxSession(config: Config(role: .master))
        let phone = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(desktop, phone)
        try desktop.start()
        try phone.start()

        let payload = Data((0..<4096).map { UInt8($0 % 251) })
        try desktop.send(payload, streamID: 77, qos: .reliable, priority: .critical, flags: [.fin])

        guard let received = try phone.receive(timeoutMS: 1000) else {
            throw NSError(domain: "ExampleLargePayload", code: 1, userInfo: [NSLocalizedDescriptionKey: "Timed out"])
        }

        let inputChecksum = payload.reduce(UInt32(0)) { $0 &+ UInt32($1) }
        let outputChecksum = received.data.reduce(UInt32(0)) { $0 &+ UInt32($1) }

        print("rx_len=\(received.data.count) stream=\(received.streamID) flags=\(received.flags.rawValue)")
        print("checksum_in=\(inputChecksum) checksum_out=\(outputChecksum) match=\(payload == received.data)")
    }
}
