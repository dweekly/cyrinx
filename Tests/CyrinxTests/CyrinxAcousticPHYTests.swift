import CCyrinx
import XCTest

@testable import Cyrinx

final class CyrinxAcousticPHYTests: XCTestCase {
    private struct CoreFrameDescriptor {
        let frameType: UInt8
        let gearID: UInt8
        let seq: UInt16
        let ack: UInt16
        let streamID: UInt16
        let priority: UInt8
        let flags: UInt8
    }

    func testAcousticPHYTurboRoundTrip() throws {
        let link = AcousticPHYLink(config: Config(sampleRateHz: 48_000))
        let payload = Data((0..<128).map { UInt8(($0 * 7) % 251) })
        let frame = makeCoreFrame(
            descriptor: CoreFrameDescriptor(
                frameType: UInt8(CYRINX_FRAME_DATA.rawValue),
                gearID: UInt8(CYRINX_GEAR_G3_16QAM.rawValue),
                seq: 4,
                ack: 0,
                streamID: 17,
                priority: 2,
                flags: UInt8(CYRINX_FLAG_FRAG_START | CYRINX_FLAG_FRAG_END | CYRINX_STREAM_FLAG_FIN)
            ),
            payload: Array(payload)
        )

        let waveform = try link.encode(frame: frame)
        XCTAssertFalse(waveform.isEmpty)

        let decoded = link.ingest(samples: waveform)
        XCTAssertEqual(decoded.count, 1)
        guard decoded.count == 1 else {
            return
        }
        XCTAssertEqual(decoded[0].frame, frame)
        XCTAssertGreaterThan(decoded[0].report.snr_db, 5.0)
    }

    func testAcousticPHYRobustRoundTripChunked() throws {
        let link = AcousticPHYLink(config: Config(sampleRateHz: 48_000))
        let ackPayload = Data((0..<10).map { UInt8($0) })
        let frame = makeCoreFrame(
            descriptor: CoreFrameDescriptor(
                frameType: UInt8(CYRINX_FRAME_ACK.rawValue),
                gearID: UInt8(CYRINX_GEAR_G2_ROBUST.rawValue),
                seq: 9,
                ack: 8,
                streamID: UInt16(CYRINX_STREAM_CONTROL),
                priority: 3,
                flags: 0
            ),
            payload: Array(ackPayload)
        )

        let waveform = try link.encode(frame: frame)
        XCTAssertFalse(waveform.isEmpty)

        var decoded: [AcousticDecodedFrame] = []
        var cursor = 0
        while cursor < waveform.count {
            let chunkSize = min(311, waveform.count - cursor)
            let chunk = Array(waveform[cursor..<(cursor + chunkSize)])
            decoded.append(contentsOf: link.ingest(samples: chunk))
            cursor += chunkSize
        }

        XCTAssertEqual(decoded.count, 1)
        guard decoded.count == 1 else {
            return
        }
        XCTAssertEqual(decoded[0].frame, frame)
    }

    func testAcousticPHYDecodedDataFrameIsAcceptedByCoreIngest() throws {
        var cConfig = cyrinx_config_t()
        cyrinx_default_config(&cConfig)
        cConfig.role = CYRINX_ROLE_SLAVE

        guard let rxSession = cyrinx_open(&cConfig) else {
            XCTFail("failed to open rx session")
            return
        }
        defer { cyrinx_close(rxSession) }
        XCTAssertEqual(cyrinx_start(rxSession), CYRINX_OK.rawValue)

        let appPayload = Data("phy-stack-e2e".utf8)
        let frame = makeCoreFrame(
            descriptor: CoreFrameDescriptor(
                frameType: UInt8(CYRINX_FRAME_DATA.rawValue),
                gearID: UInt8(CYRINX_GEAR_G3_QPSK.rawValue),
                seq: 21,
                ack: 0,
                streamID: 42,
                priority: 1,
                flags: UInt8(CYRINX_FLAG_FRAG_START | CYRINX_FLAG_FRAG_END | CYRINX_STREAM_FLAG_FIN)
            ),
            payload: Array(appPayload)
        )

        let link = AcousticPHYLink(config: Config(sampleRateHz: 48_000))
        let waveform = try link.encode(frame: frame)
        let decoded = link.ingest(samples: waveform)
        XCTAssertEqual(decoded.count, 1)
        guard decoded.count == 1 else {
            return
        }

        var report = decoded[0].report
        let ingestRC = decoded[0].frame.withUnsafeBufferPointer { ptr in
            cyrinx_ingest_frame(rxSession, ptr.baseAddress, ptr.count, &report)
        }
        XCTAssertEqual(ingestRC, CYRINX_OK.rawValue)

        var out = [UInt8](repeating: 0, count: 256)
        var outLen = out.count
        var meta = cyrinx_message_meta_t(stream_id: 0, priority: 0, flags: 0, payload_len: 0)
        let recvRC = cyrinx_recv_stream(rxSession, &out, &outLen, 10, &meta)
        XCTAssertEqual(recvRC, CYRINX_OK.rawValue)
        XCTAssertEqual(Data(out.prefix(outLen)), appPayload)
        XCTAssertEqual(meta.stream_id, 42)
        XCTAssertEqual(meta.priority, 1)
        XCTAssertEqual(meta.flags, UInt8(CYRINX_STREAM_FLAG_FIN))
    }

    private func makeCoreFrame(descriptor: CoreFrameDescriptor, payload: [UInt8]) -> [UInt8] {
        var header = [UInt8](repeating: 0, count: 15)
        var bitPos = 0
        writeBits(&header, &bitPos, UInt32(1), 4)  // version
        writeBits(&header, &bitPos, UInt32(descriptor.frameType & 0xF), 4)
        writeBits(&header, &bitPos, 0x00A1B2, 24)  // synthetic session id
        writeBits(&header, &bitPos, UInt32(descriptor.seq), 16)
        writeBits(&header, &bitPos, UInt32(descriptor.ack), 16)
        writeBits(&header, &bitPos, UInt32(descriptor.gearID & 0x7), 3)
        writeBits(&header, &bitPos, 2, 3)  // synthetic fec rate
        writeBits(&header, &bitPos, UInt32(descriptor.streamID & 0x0FFF), 12)
        writeBits(&header, &bitPos, UInt32(descriptor.priority & 0x3), 2)
        writeBits(&header, &bitPos, UInt32(payload.count & 0x0FFF), 12)
        writeBits(&header, &bitPos, UInt32(descriptor.flags), 8)

        let headerCRC = crc16CCITT(Array(header.prefix(13)))
        writeBits(&header, &bitPos, UInt32(headerCRC), 16)

        var frame: [UInt8] = [0xC7, 0x58]
        frame.append(contentsOf: header)
        frame.append(contentsOf: payload)
        let frameCRC = crc32C(frame)
        frame.append(UInt8((frameCRC >> 24) & 0xFF))
        frame.append(UInt8((frameCRC >> 16) & 0xFF))
        frame.append(UInt8((frameCRC >> 8) & 0xFF))
        frame.append(UInt8(frameCRC & 0xFF))
        return frame
    }

    private func writeBits(_ bytes: inout [UInt8], _ bitPos: inout Int, _ value: UInt32, _ count: Int) {
        for idx in 0..<count {
            let shift = count - 1 - idx
            let bit = (value >> UInt32(shift)) & 1
            let byteIndex = bitPos / 8
            let bitIndex = 7 - (bitPos % 8)
            if bit == 1 {
                bytes[byteIndex] |= 1 << UInt8(bitIndex)
            }
            bitPos += 1
        }
    }

    private func crc16CCITT(_ data: [UInt8]) -> UInt16 {
        var crc: UInt16 = 0xFFFF
        for byte in data {
            crc ^= UInt16(byte) << 8
            for _ in 0..<8 {
                if (crc & 0x8000) != 0 {
                    crc = (crc << 1) ^ 0x1021
                } else {
                    crc <<= 1
                }
            }
        }
        return crc
    }

    private func crc32C(_ data: [UInt8]) -> UInt32 {
        var crc: UInt32 = 0xFFFFFFFF
        for byte in data {
            crc ^= UInt32(byte)
            for _ in 0..<8 {
                let mask: UInt32 = (crc & 1) == 1 ? 0xFFFFFFFF : 0
                crc = (crc >> 1) ^ (0x82F63B78 & mask)
            }
        }
        return ~crc
    }
}
