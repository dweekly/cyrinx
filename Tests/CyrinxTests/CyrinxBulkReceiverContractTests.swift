import CCyrinx
import Foundation
import XCTest

@testable import Cyrinx

final class CyrinxBulkReceiverContractTests: XCTestCase {
    func testLoadedBinaryReportsFixedReceiverContract() {
        XCTAssertEqual(MemoryLayout<cyrinx_bulk_receiver_contract_v1>.size, 72)

        var contract = cyrinx_bulk_receiver_contract_v1()
        XCTAssertEqual(
            cyrinx_bulk_get_receiver_contract_v1(
                &contract, MemoryLayout<cyrinx_bulk_receiver_contract_v1>.size),
            0)
        XCTAssertEqual(contract.struct_size, 72)
        XCTAssertEqual(
            contract.abi_version, UInt32(CYRINX_BULK_RECEIVER_CONTRACT_ABI_VERSION))
        XCTAssertEqual(
            contract.semantics_version, UInt32(CYRINX_BULK_RECEIVER_SEMANTICS_VERSION))
        XCTAssertEqual(
            contract.reliability_estimator,
            UInt32(CYRINX_BULK_RECEIVER_ESTIMATOR_KNOWN_PILOT_LOCAL_LINEAR_BOXCAR_V1))
        XCTAssertEqual(contract.local_pilot_window, 11)
        XCTAssertEqual(contract.edge_mode, UInt32(CYRINX_BULK_RECEIVER_EDGE_REPLICATE))
        XCTAssertEqual(contract.global_weight_numerator, 25)
        XCTAssertEqual(contract.local_weight_numerator, 75)
        XCTAssertEqual(contract.weight_denominator, 100)
        XCTAssertEqual(
            contract.final_comb_mode, UInt32(CYRINX_BULK_RECEIVER_FINAL_COMB_EXTEND_LAST))
        XCTAssertEqual(contract.snr_floor, 0.1, accuracy: 0)
        XCTAssertEqual(contract.nonfinite_residual_ceiling, 1e9, accuracy: 0)
        XCTAssertEqual(contract.reserved.0, 0)
        XCTAssertEqual(contract.reserved.1, 0)
        XCTAssertEqual(contract.reserved.2, 0)
        XCTAssertEqual(contract.reserved.3, 0)
    }

    func testReceiverContractSizeMismatchDoesNotModifyOutput() {
        var contract = cyrinx_bulk_receiver_contract_v1()
        contract.struct_size = 0xA5A5_A5A5
        contract.abi_version = 0x5A5A_5A5A
        contract.snr_floor = 123.5
        let before = withUnsafeBytes(of: contract) { Array($0) }

        XCTAssertEqual(
            cyrinx_bulk_get_receiver_contract_v1(
                &contract, MemoryLayout<cyrinx_bulk_receiver_contract_v1>.size - 1),
            -1)
        XCTAssertEqual(withUnsafeBytes(of: contract) { Array($0) }, before)
        XCTAssertEqual(
            cyrinx_bulk_get_receiver_contract_v1(
                &contract, MemoryLayout<cyrinx_bulk_receiver_contract_v1>.size + 1),
            -1)
        XCTAssertEqual(withUnsafeBytes(of: contract) { Array($0) }, before)
        XCTAssertEqual(
            cyrinx_bulk_get_receiver_contract_v1(
                nil, MemoryLayout<cyrinx_bulk_receiver_contract_v1>.size),
            -1)
    }

    func testReliabilityDiagnosticReplicatesLowerAndUpperEdgeImpulses() {
        var lower = [Double](repeating: 0, count: 11)
        lower[0] = 1
        let lowerResult = diagnose(lower)
        XCTAssertEqual(lowerResult.status, 0)
        assertEqual(
            lowerResult.smoothed,
            [6, 5, 4, 3, 2, 1, 0, 0, 0, 0, 0].map { $0 / 11 })

        var upper = [Double](repeating: 0, count: 11)
        upper[10] = 1
        let upperResult = diagnose(upper)
        XCTAssertEqual(upperResult.status, 0)
        assertEqual(
            upperResult.smoothed,
            [0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6].map { $0 / 11 })
    }

    func testReliabilityDiagnosticPinsRampInterpolationAndFinalCombExtension() {
        let positions: [Int32] = [0, 4, 8, 12, 79, 80, 87]
        let result = diagnose((0...10).map(Double.init), positions: positions)
        XCTAssertEqual(result.status, 0)
        assertEqual(
            result.smoothed,
            [15, 21, 28, 36, 45, 55, 65, 74, 82, 89, 95].map { $0 / 11 })
        assertEqual(
            result.interpolated,
            [15, 18, 21, 24.5, 94.25, 95, 95].map { $0 / 11 })
    }

    func testReliabilityDiagnosticPinsWindowLargerThanPilotCount() {
        let result = diagnose([1, 2, 3], positions: [0, 4, 8, 12, 16, 23])
        XCTAssertEqual(result.status, 0)
        assertEqual(result.smoothed, [20, 22, 24].map { $0 / 11 })
        assertEqual(result.interpolated, [20, 21, 22, 23, 24, 24].map { $0 / 11 })
    }

    func testReliabilityDiagnosticRejectsInvalidInputWithoutPartialOutput() {
        let cases:
            [(
                name: String, pilots: [Double], pilotEvery: Int32, positions: [Int32],
                smoothedCapacity: Int, interpolationCapacity: Int
            )] = [
                ("NaN residual", [0, .nan, 2], 8, [0, 4], 3, 2),
                ("infinite residual", [0, .infinity, 2], 8, [0, 4], 3, 2),
                ("negative residual", [0, -0.1, 2], 8, [0, 4], 3, 2),
                ("zero pilot spacing", [0, 1, 2], 0, [0, 4], 3, 2),
                ("negative position", [0, 1, 2], 8, [0, -1], 3, 2),
                ("short smoothing output", [0, 1, 2], 8, [0, 4], 2, 2),
                ("short interpolation output", [0, 1, 2], 8, [0, 4], 3, 1),
            ]
        for testCase in cases {
            var smoothed = [Double](repeating: -7, count: testCase.pilots.count)
            var interpolated = [Double](repeating: -9, count: testCase.positions.count)
            let status = testCase.pilots.withUnsafeBufferPointer { pilotBuffer in
                testCase.positions.withUnsafeBufferPointer { positionBuffer in
                    smoothed.withUnsafeMutableBufferPointer { smoothBuffer in
                        interpolated.withUnsafeMutableBufferPointer { interpolationBuffer in
                            cyrinx_bulk_receiver_reliability_diagnostic_v1(
                                pilotBuffer.baseAddress, Int32(testCase.pilots.count),
                                testCase.pilotEvery, positionBuffer.baseAddress,
                                testCase.positions.count, smoothBuffer.baseAddress,
                                testCase.smoothedCapacity, interpolationBuffer.baseAddress,
                                testCase.interpolationCapacity)
                        }
                    }
                }
            }
            XCTAssertEqual(status, -1, testCase.name)
            XCTAssertEqual(
                smoothed, [Double](repeating: -7, count: testCase.pilots.count),
                testCase.name)
            XCTAssertEqual(
                interpolated, [Double](repeating: -9, count: testCase.positions.count),
                testCase.name)
        }
    }

    func testNonfiniteDataSymbolsProduceZeroConfidenceInsteadOfNaNLLRs() {
        let configuration = BulkPHY.Configuration(
            bitsPerBin: 1, rate: "1/2", symbolCount: 8, cyclicPrefix: 96,
            amplitude: 0.18)
        let phy = BulkPHY(configuration: configuration)
        guard let geometry = phy.geometry() else { return XCTFail("geometry nil") }
        let payload = Data((0..<geometry.payloadBytes).map { UInt8(($0 * 31 + 7) & 0xFF) })
        guard let wave = phy.encode(payload) else { return XCTFail("encode nil") }

        let symbolSamples = configuration.fftSize + configuration.cyclicPrefix
        let dataStart =
            Int(CYRINX_BULK_CHIRP_LEN) + Int(CYRINX_BULK_GUARD) + 2 * symbolSamples
        var erased = wave
        for index in dataStart..<(dataStart + symbolSamples) {
            erased[index] = 0
        }

        guard let erasedDecode = phy.decode(capture(erased)) else {
            return XCTFail("erased decode nil")
        }
        for contaminant: Float in [.nan, .infinity, -.infinity] {
            var nonfinite = wave
            for index in dataStart..<(dataStart + symbolSamples) {
                nonfinite[index] = contaminant
            }
            guard let nonfiniteDecode = phy.decode(capture(nonfinite)) else {
                return XCTFail("nonfinite decode nil")
            }
            XCTAssertEqual(nonfiniteDecode.payload, erasedDecode.payload)
            XCTAssertEqual(nonfiniteDecode.blocksOK, erasedDecode.blocksOK)
            XCTAssertEqual(nonfiniteDecode.blockCount, erasedDecode.blockCount)
            XCTAssertTrue(nonfiniteDecode.evmRMS.isFinite)
            XCTAssertGreaterThan(nonfiniteDecode.evmRMS, erasedDecode.evmRMS)
        }
    }

    private func diagnose(
        _ pilots: [Double], positions: [Int32] = [], pilotEvery: Int32 = 8
    ) -> (status: Int32, smoothed: [Double], interpolated: [Double]) {
        var smoothed = [Double](repeating: -.infinity, count: pilots.count)
        var interpolated = [Double](repeating: -.infinity, count: positions.count)
        let smoothedCapacity = smoothed.count
        let interpolationCapacity = interpolated.count
        let status = pilots.withUnsafeBufferPointer { pilotBuffer in
            positions.withUnsafeBufferPointer { positionBuffer in
                smoothed.withUnsafeMutableBufferPointer { smoothBuffer in
                    interpolated.withUnsafeMutableBufferPointer { interpolationBuffer in
                        cyrinx_bulk_receiver_reliability_diagnostic_v1(
                            pilotBuffer.baseAddress, Int32(pilots.count), pilotEvery,
                            positions.isEmpty ? nil : positionBuffer.baseAddress, positions.count,
                            smoothBuffer.baseAddress, smoothedCapacity,
                            positions.isEmpty ? nil : interpolationBuffer.baseAddress,
                            interpolationCapacity)
                    }
                }
            }
        }
        return (status, smoothed, interpolated)
    }

    private func assertEqual(
        _ actual: [Double], _ expected: [Double], accuracy: Double = 1e-12,
        file: StaticString = #filePath, line: UInt = #line
    ) {
        XCTAssertEqual(actual.count, expected.count, file: file, line: line)
        for (actualValue, expectedValue) in zip(actual, expected) {
            XCTAssertEqual(actualValue, expectedValue, accuracy: accuracy, file: file, line: line)
        }
    }

    private func capture(_ wave: [Float]) -> [Float] {
        var result = [Float](repeating: 0, count: 3000)
        result.append(contentsOf: wave)
        result.append(contentsOf: [Float](repeating: 0, count: 2000))
        return result
    }
}
