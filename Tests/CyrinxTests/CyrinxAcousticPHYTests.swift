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

    func testMIMOSVDAndTHD() throws {
        // 1. Verify SVD solver on high condition number (highly correlated MIMO channel, should choose Diversity)
        let configStereo = Config(sampleRateHz: 48_000, channels: 2)
        let linkStereo = AcousticPHYLink(config: configStereo)
        
        let pL = try linkStereo.encode(frame: [1, 2, 3])
        let preambleLen = 1016
        var mockLeft = [Float](repeating: 0, count: preambleLen)
        var mockRight = [Float](repeating: 0, count: preambleLen)
        
        var x1 = [Float](repeating: 0, count: preambleLen)
        var x2 = [Float](repeating: 0, count: preambleLen)
        for i in 0..<preambleLen {
            x1[i] = pL[i * 2]
            x2[i] = pL[i * 2 + 1]
        }
        
        for i in 0..<preambleLen {
            mockLeft[i] = 1.0 * x1[i] + 0.95 * x2[i]
            mockRight[i] = 0.95 * x1[i] + 1.0 * x2[i]
        }
        
        var mockInterleaved = [Float](repeating: 0, count: preambleLen * 2)
        for i in 0..<preambleLen {
            mockInterleaved[i * 2] = mockLeft[i]
            mockInterleaved[i * 2 + 1] = mockRight[i]
        }
        
        _ = linkStereo.ingest(samples: mockInterleaved)
        
        XCTAssertGreaterThan(linkStereo.lastH11, 0.65)
        XCTAssertGreaterThan(linkStereo.lastH22, 0.65)
        XCTAssertGreaterThan(linkStereo.lastH12, 0.60)
        XCTAssertGreaterThan(linkStereo.lastH21, 0.60)
        XCTAssertEqual(linkStereo.lastSpatialMode, 0)
        
        // 2. Verify SVD solver on orthogonal/low condition number (should choose Multiplexing)
        let linkStereo2 = AcousticPHYLink(config: configStereo)
        for i in 0..<preambleLen {
            mockLeft[i] = 1.0 * x1[i] + 0.05 * x2[i]
            mockRight[i] = 0.05 * x1[i] + 1.0 * x2[i]
        }
        for i in 0..<preambleLen {
            mockInterleaved[i * 2] = mockLeft[i]
            mockInterleaved[i * 2 + 1] = mockRight[i]
        }
        _ = linkStereo2.ingest(samples: mockInterleaved)
        XCTAssertEqual(linkStereo2.lastSpatialMode, 1)
        
        // 3. Verify THD measurement math on a pure 3kHz sine tone vs distorted tone
        let fs: Float = 48000
        let f0: Float = 3000
        let cleanTone = AcousticPHYLink.generateSineTone(frequencyHz: f0, durationSecs: 0.1, sampleRateHz: fs, amplitude: 0.5)
        let cleanTHD = AcousticPHYLink.calculateTHD(samples: cleanTone, sampleRateHz: Int(fs), fundamentalHz: f0)
        XCTAssertLessThan(cleanTHD, 0.5)
        
        var distortedTone = cleanTone
        let h2Tone = AcousticPHYLink.generateSineTone(frequencyHz: f0 * 2, durationSecs: 0.1, sampleRateHz: fs, amplitude: 0.05)
        let h3Tone = AcousticPHYLink.generateSineTone(frequencyHz: f0 * 3, durationSecs: 0.1, sampleRateHz: fs, amplitude: 0.025)
        for i in 0..<distortedTone.count {
            distortedTone[i] += h2Tone[i] + h3Tone[i]
        }
        let distTHD = AcousticPHYLink.calculateTHD(samples: distortedTone, sampleRateHz: Int(fs), fundamentalHz: f0)
        XCTAssertGreaterThan(distTHD, 9.0)
        XCTAssertLessThan(distTHD, 13.0)
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
