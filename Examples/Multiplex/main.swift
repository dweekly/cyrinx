import Cyrinx
import Foundation

@main
struct MultiplexExample {
    static func main() throws {
        let sender = try CyrinxSession(config: Config(role: .master))
        let receiver = try CyrinxSession(config: Config(role: .slave))

        try CyrinxSession.linkInMemory(sender, receiver)
        try sender.start()
        try receiver.start()

        struct Packet {
            let streamID: UInt16
            let priority: StreamPriority
            let flags: StreamFlags
            let body: String
        }

        let packets: [Packet] = [
            Packet(streamID: 10, priority: .low, flags: [], body: "stream-10/chunk-1"),
            Packet(streamID: 20, priority: .critical, flags: [.fin], body: "stream-20/final"),
            Packet(streamID: 10, priority: .high, flags: [.fin], body: "stream-10/final")
        ]

        for packet in packets {
            try sender.send(
                Data(packet.body.utf8),
                streamID: packet.streamID,
                qos: .reliable,
                priority: packet.priority,
                flags: packet.flags
            )
        }

        var receivedByStream: [UInt16: [String]] = [:]

        for _ in packets {
            guard let received = try receiver.receive(timeoutMS: 500) else {
                throw NSError(domain: "ExampleMultiplex", code: 1, userInfo: [NSLocalizedDescriptionKey: "Timed out"])
            }

            let text = String(decoding: received.data, as: UTF8.self)
            receivedByStream[received.streamID, default: []].append(text)

            print(
                "rx stream=\(received.streamID) priority=\(received.priority.rawValue) flags=\(received.flags.rawValue) payload='\(text)'"
            )
        }

        print("summary=\(receivedByStream)")
    }
}
