import CCyrinx
import Cyrinx
import Foundation
import Testing

@Suite("Cyrinx Batch Encoding & Decoding Tests")
struct CyrinxBatchTests {

    @Test("Test profile 1 batch round-trip")
    func testProfile1RoundTrip() throws {
        guard let profile = CyrinxProfileRegistry.get(id: 1) else {
            Issue.record("Profile 1 not found")
            return
        }

        let phy = BulkPHY(configuration: profile.toBulkConfiguration())
        let geom = phy.geometry()!
        let payloadBytes = geom.payloadBytes
        let payload = Data((0..<payloadBytes).map { i in UInt8((i * 31 + 7) & 0xFF) })

        // Encode via CyrinxBatch
        let (samples, encResult) = try CyrinxBatch.encode(profile: profile, payload: payload)

        #expect(!samples.isEmpty)
        #expect(encResult.profileID == 1)
        #expect(encResult.producedSamples == samples.count)
        #expect(encResult.blocksOk == encResult.blocksTotal)
        #expect(encResult.clippingEvidence == 0)
        #expect(encResult.nonfiniteEvidence == 0)

        // Pad loopback samples
        var rxSamples = [Float](repeating: 0.0, count: 3000)
        rxSamples.append(contentsOf: samples)
        rxSamples.append(contentsOf: [Float](repeating: 0.0, count: 2000))

        // Decode via BulkPHY directly to cross-validate CyrinxBatch.encode output
        if let decodedByPhy = phy.decode(rxSamples) {
            #expect(decodedByPhy.isComplete)
        } else {
            Issue.record("Phy failed to decode Batch wave")
        }

        // Encode via BulkPHY directly to cross-validate CyrinxBatch.decode input
        let phySamples = phy.encode(payload)!
        var rxPhySamples = [Float](repeating: 0.0, count: 3000)
        rxPhySamples.append(contentsOf: phySamples)
        rxPhySamples.append(contentsOf: [Float](repeating: 0.0, count: 2000))

        // Decode via CyrinxBatch
        let layout = CyrinxBatchInputLayout(
            primarySamples: rxPhySamples,
            channelCount: 1,
            channelStride: 1
        )
        let (decoded, decResult) = try CyrinxBatch.decode(profile: profile, input: layout)

        #expect(decResult.profileID == 1)
        #expect(decResult.blocksOk == decResult.blocksTotal)
        #expect(decResult.clippingEvidence == 0)
        #expect(decResult.nonfiniteEvidence == 0)
        #expect(decResult.selectedReceiver == 0)  // primary
        #expect(decResult.selectionReason == 6)  // CYRINX_BULK_AUTO_REASON_SECOND_UNAVAILABLE
        #expect(decoded == payload)
    }

    @Test("Test profile 3 batch round-trip")
    func testProfile3RoundTrip() throws {
        guard let profile = CyrinxProfileRegistry.get(id: 3) else {
            Issue.record("Profile 3 not found")
            return
        }

        let phy = BulkPHY(configuration: profile.toBulkConfiguration())
        let geom = phy.geometry()!
        let payloadBytes = geom.payloadBytes
        let payload = Data((0..<payloadBytes).map { i in UInt8((i * 31 + 7) & 0xFF) })

        // Encode via CyrinxBatch
        let (samples, _) = try CyrinxBatch.encode(profile: profile, payload: payload)

        // Pad loopback samples
        var rxSamples = [Float](repeating: 0.0, count: 3000)
        rxSamples.append(contentsOf: samples)
        rxSamples.append(contentsOf: [Float](repeating: 0.0, count: 2000))

        // Decode via BulkPHY directly to cross-validate CyrinxBatch.encode output
        if let decodedByPhy = phy.decode(rxSamples, combining: rxSamples) {
            #expect(decodedByPhy.isComplete)
        } else {
            Issue.record("Phy failed to decode Batch wave (Stereo)")
        }

        // Encode via BulkPHY directly to cross-validate CyrinxBatch.decode input
        let phySamples = phy.encode(payload)!
        var rxPhySamples = [Float](repeating: 0.0, count: 3000)
        rxPhySamples.append(contentsOf: phySamples)
        rxPhySamples.append(contentsOf: [Float](repeating: 0.0, count: 2000))

        // Decode via CyrinxBatch (Stereo - Identical Channels)
        let layout = CyrinxBatchInputLayout(
            primarySamples: rxPhySamples,
            secondarySamples: rxPhySamples,
            channelCount: 2,
            channelStride: 1
        )
        let (decoded, decResult) = try CyrinxBatch.decode(profile: profile, input: layout)

        #expect(decResult.profileID == 3)
        #expect(decResult.blocksOk == decResult.blocksTotal)
        #expect(decResult.selectedReceiver == 0)  // primary (fails closed because MRC isn't 5% better)
        #expect(decResult.selectionReason == 2)  // CYRINX_BULK_AUTO_REASON_PRIMARY_MARGIN_NOT_MET
        #expect(decoded == payload)
    }

    @Test("Test safety check: clipping detection")
    func testClippingDetection() throws {
        guard let profile = CyrinxProfileRegistry.get(id: 1) else {
            Issue.record("Profile 1 not found")
            return
        }

        // Generate normal samples but insert clipping evidence
        var samples = [Float](repeating: 0.1, count: 200000)
        samples[100] = 0.999
        samples[200] = -1.0
        samples[300] = 1.2

        let layout = CyrinxBatchInputLayout(
            primarySamples: samples,
            channelCount: 1,
            channelStride: 1
        )

        // Since the samples are just dummy noise, decode might fail, but let's check safety metrics in the result
        do {
            _ = try CyrinxBatch.decode(profile: profile, input: layout)
        } catch {
            // It might throw a CRC error, which is expected for dummy samples
        }

        // Call the C decode directly to inspect result without throwing Swift exceptions
        var rawProfile = profile.toCStruct()
        var rawInput = cyrinx_batch_input_layout_t()
        rawInput.struct_size = MemoryLayout<cyrinx_batch_input_layout_t>.size
        rawInput.abi_version = 1
        rawInput.primary_count = samples.count
        rawInput.channel_count = 1
        rawInput.channel_stride = 1

        var payload = Data(repeating: 0, count: 20000)
        var rawResult = cyrinx_batch_result_t()
        rawResult.struct_size = MemoryLayout<cyrinx_batch_result_t>.size
        rawResult.abi_version = 1

        let rc = samples.withUnsafeBufferPointer { samplesPtr in
            rawInput.primary_samples = samplesPtr.baseAddress
            return payload.withUnsafeMutableBytes { payloadPtr in
                cyrinx_batch_decode(
                    &rawProfile,
                    &rawInput,
                    payloadPtr.bindMemory(to: UInt8.self).baseAddress,
                    payloadPtr.count,
                    &rawResult
                )
            }
        }

        #expect(rawResult.clipping_evidence == 3)
        #expect(rawResult.nonfinite_evidence == 0)
    }

    @Test("Test safety check: non-finite detection")
    func testNonFiniteDetection() throws {
        guard let profile = CyrinxProfileRegistry.get(id: 1) else {
            Issue.record("Profile 1 not found")
            return
        }

        var samples = [Float](repeating: 0.1, count: 200000)
        samples[150] = Float.nan
        samples[250] = Float.infinity
        samples[350] = -Float.infinity

        // Call C decode directly to inspect rawResult
        var rawProfile = profile.toCStruct()
        var rawInput = cyrinx_batch_input_layout_t()
        rawInput.struct_size = MemoryLayout<cyrinx_batch_input_layout_t>.size
        rawInput.abi_version = 1
        rawInput.primary_count = samples.count
        rawInput.channel_count = 1
        rawInput.channel_stride = 1

        var payload = Data(repeating: 0, count: 20000)
        var rawResult = cyrinx_batch_result_t()
        rawResult.struct_size = MemoryLayout<cyrinx_batch_result_t>.size
        rawResult.abi_version = 1

        let rc = samples.withUnsafeBufferPointer { samplesPtr in
            rawInput.primary_samples = samplesPtr.baseAddress
            return payload.withUnsafeMutableBytes { payloadPtr in
                cyrinx_batch_decode(
                    &rawProfile,
                    &rawInput,
                    payloadPtr.bindMemory(to: UInt8.self).baseAddress,
                    payloadPtr.count,
                    &rawResult
                )
            }
        }

        #expect(rawResult.clipping_evidence == 0)
        #expect(rawResult.nonfinite_evidence == 3)
    }

    @Test("Test C API ABI size and version validation")
    func testABIVersionValidation() throws {
        guard let profile = CyrinxProfileRegistry.get(id: 1) else {
            Issue.record("Profile 1 not found")
            return
        }

        var rawProfile = profile.toCStruct()
        var rawInput = cyrinx_batch_input_layout_t()
        rawInput.struct_size = MemoryLayout<cyrinx_batch_input_layout_t>.size
        rawInput.abi_version = 999  // Invalid ABI version

        let samples = [Float](repeating: 0.0, count: 10000)
        rawInput.primary_count = samples.count
        rawInput.channel_count = 1
        rawInput.channel_stride = 1

        var payload = Data(repeating: 0, count: 20000)
        var rawResult = cyrinx_batch_result_t()
        rawResult.struct_size = MemoryLayout<cyrinx_batch_result_t>.size
        rawResult.abi_version = 1

        let rc = samples.withUnsafeBufferPointer { samplesPtr in
            rawInput.primary_samples = samplesPtr.baseAddress
            return payload.withUnsafeMutableBytes { payloadPtr in
                cyrinx_batch_decode(
                    &rawProfile,
                    &rawInput,
                    payloadPtr.bindMemory(to: UInt8.self).baseAddress,
                    payloadPtr.count,
                    &rawResult
                )
            }
        }

        // Should return CYRINX_ERR_INVALID_ARGUMENT (-1)
        #expect(rc == CYRINX_ERR_INVALID_ARGUMENT)
    }

    @Test("Test connection send queue limit and size check")
    func testConnectionSendQueueAndSizeLimit() async throws {
        let peer = CyrinxPeer(displayName: "TestPeer")
        let connection = CyrinxConnection(peer: peer, state: .connected)

        // Test sending payload exceeding 64KB
        let tooLargeData = Data(repeating: 0, count: 65537)
        await #expect(
            performing: {
                _ = try await connection.send(tooLargeData)
            },
            throws: { error in
                guard let cyrinxErr = error as? CyrinxError else { return false }
                return cyrinxErr == .invalidArgument
            })

        // Fill queue with 16 transfers
        var transfers: [CyrinxTransfer] = []
        let validData = Data("test data".utf8)
        for _ in 0..<16 {
            let t = try await connection.send(validData)
            transfers.append(t)
        }

        // The 17th transfer should trigger queueFull error
        await #expect(
            performing: {
                _ = try await connection.send(validData)
            },
            throws: { error in
                guard let cyrinxErr = error as? CyrinxError else { return false }
                return cyrinxErr == .queueFull
            })
    }
}
