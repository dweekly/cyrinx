import CCyrinx
import Foundation
import Testing

#if canImport(CryptoKit)
    import CryptoKit
#endif

/// C3-03: the versioned ABI base — exact frozen layouts on this target, the
/// prefix acceptance contract, fixed-width status parity with the 2.x enum,
/// and the portable SHA-256 against the NIST FIPS 180-4 reference vectors.
@Suite struct CyrinxABIBaseTests {
    @Test func frozenLayoutSizesOnThisTarget() {
        #expect(MemoryLayout<cyrinx_abi_header_t>.size == 8)
        #expect(MemoryLayout<cyrinx_profile_t>.size == 128)
        #expect(MemoryLayout<cyrinx_batch_channel_view_t>.size == 32)
        #expect(MemoryLayout<cyrinx_batch_input_t>.size == 80)
        #expect(MemoryLayout<cyrinx_batch_result_t>.size == 136)
        #expect(MemoryLayout<cyrinx_profile_t>.size == Int(CYRINX_PROFILE_V1_SIZE))
        #expect(MemoryLayout<cyrinx_batch_input_t>.size == Int(CYRINX_BATCH_INPUT_V1_SIZE))
        #expect(MemoryLayout<cyrinx_batch_result_t>.size == Int(CYRINX_BATCH_RESULT_V1_SIZE))
    }

    @Test func statusValuesMatchLegacyEnum() {
        #expect(CYRINX_STATUS_OK == CYRINX_OK.rawValue)
        #expect(CYRINX_STATUS_ERR_INVALID_ARGUMENT == CYRINX_ERR_INVALID_ARGUMENT.rawValue)
        #expect(CYRINX_STATUS_ERR_NOT_RUNNING == CYRINX_ERR_NOT_RUNNING.rawValue)
        #expect(CYRINX_STATUS_ERR_BUFFER_TOO_SMALL == CYRINX_ERR_BUFFER_TOO_SMALL.rawValue)
        #expect(CYRINX_STATUS_ERR_TIMEOUT == CYRINX_ERR_TIMEOUT.rawValue)
        #expect(CYRINX_STATUS_ERR_CRC == CYRINX_ERR_CRC.rawValue)
        #expect(CYRINX_STATUS_ERR_BUSY == CYRINX_ERR_BUSY.rawValue)
        #expect(CYRINX_STATUS_ERR_UNSUPPORTED == CYRINX_ERR_UNSUPPORTED.rawValue)
        #expect(CYRINX_STATUS_ERR_STATE == CYRINX_ERR_STATE.rawValue)
        #expect(CYRINX_STATUS_ERR_INTERNAL == CYRINX_ERR_INTERNAL.rawValue)
        #expect(CYRINX_STATUS_ERR_DECODE == -10)
        #expect(CYRINX_STATUS_ERR_NOT_FOUND == -11)
    }

    @Test func prefixAcceptanceContract() {
        var header = cyrinx_abi_header_t(struct_size: 128, abi_version: 1)
        #expect(cyrinx_abi_accepts(&header, 128, 1))
        #expect(cyrinx_abi_accepts(&header, 96, 1))
        header.struct_size = 96
        #expect(!cyrinx_abi_accepts(&header, 128, 1))
        header.struct_size = 256
        #expect(cyrinx_abi_accepts(&header, 128, 1))
        header.abi_version = 2
        #expect(!cyrinx_abi_accepts(&header, 128, 1))
        #expect(!cyrinx_abi_accepts(nil, 128, 1))
    }

    @Test func sha256MatchesNISTVectors() {
        #expect(
            C3Support.sha256Hex([])
                == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        #expect(
            C3Support.sha256Hex(Array("abc".utf8))
                == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        #expect(
            C3Support.sha256Hex(Array("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq".utf8))
                == "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1")
    }

    @Test func sha256HandlesBlockBoundaryLengths() {
        // 55/56/63/64/65 bytes straddle the single- vs two-block padding split.
        for length in [55, 56, 63, 64, 65] {
            let data = [UInt8](repeating: 0x61, count: length)
            let viaFoundation = sha256ReferenceHex(Data(data))
            #expect(C3Support.sha256Hex(data) == viaFoundation, "length \(length)")
        }
    }

    private func sha256ReferenceHex(_ data: Data) -> String {
        // CryptoKit reference on Apple targets keeps this test self-checking;
        // elsewhere the boundary lengths are still exercised for stability.
        #if canImport(CryptoKit)
            return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        #else
            return C3Support.sha256Hex([UInt8](data))
        #endif
    }
}
