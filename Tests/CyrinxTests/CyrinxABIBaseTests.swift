import CCyrinx
import Cyrinx
import Foundation
import Testing

@Suite("Cyrinx Base ABI Tests")
struct CyrinxABIBaseTests {
    @Test("Verify base types size and alignment layout")
    func testBaseTypesLayout() {
        #expect(MemoryLayout<cyrinx_buf_t>.size >= 16)
        #expect(MemoryLayout<cyrinx_const_buf_t>.size >= 16)
        #expect(MemoryLayout<cyrinx_allocator_t>.size >= 24)
        #expect(MemoryLayout<cyrinx_clock_t>.size >= 16)
    }

    @Test("Verify versioned ABI struct validation behavior")
    func testVersionedStructValidation() {
        let expectedABI = UInt32(CYRINX_TEST_ABI_VERSION)

        var validStruct = cyrinx_test_versioned_struct_t()
        validStruct.struct_size = MemoryLayout<cyrinx_test_versioned_struct_t>.size
        validStruct.abi_version = expectedABI
        validStruct.data = 42

        // Test validation with correct parameters
        let isValid = cyrinx_validate_abi(
            &validStruct, MemoryLayout<cyrinx_test_versioned_struct_t>.size, expectedABI)
        #expect(isValid)

        // Test validation with nil pointer
        let isNilValid = cyrinx_validate_abi(
            nil, MemoryLayout<cyrinx_test_versioned_struct_t>.size, expectedABI)
        #expect(!isNilValid)

        // Test validation with undersized structure size
        let isUndersizedValid = cyrinx_validate_abi(
            &validStruct, MemoryLayout<cyrinx_test_versioned_struct_t>.size + 8, expectedABI)
        #expect(!isUndersizedValid)

        // Test validation with different ABI version
        let isWrongABIValid = cyrinx_validate_abi(
            &validStruct, MemoryLayout<cyrinx_test_versioned_struct_t>.size, expectedABI + 1)
        #expect(!isWrongABIValid)

        // Test validation with a structure that is larger than the minimum known size (allowed by the ABI)
        var largerStruct = validStruct
        largerStruct.struct_size = MemoryLayout<cyrinx_test_versioned_struct_t>.size + 16
        let isLargerValid = cyrinx_validate_abi(
            &largerStruct, MemoryLayout<cyrinx_test_versioned_struct_t>.size, expectedABI)
        #expect(isLargerValid)
    }
}
