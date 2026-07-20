import CCyrinx
import Foundation

/// Swift wrapper mapping the C profiles, classifications, and registry lookup functions losslessly.
/// All Swift types are immutable and conform to `Sendable`.
public enum CyrinxModulation: Sendable, Codable, Equatable, Hashable {
    case bpsk
    case qpsk
    case qam16
    case qam64
    case qam256

    public init?(_ cValue: cyrinx_modulation_t) {
        switch cValue {
        case CYRINX_MODULATION_BPSK: self = .bpsk
        case CYRINX_MODULATION_QPSK: self = .qpsk
        case CYRINX_MODULATION_16QAM: self = .qam16
        case CYRINX_MODULATION_64QAM: self = .qam64
        case CYRINX_MODULATION_256QAM: self = .qam256
        default: return nil
        }
    }

    public var cValue: cyrinx_modulation_t {
        switch self {
        case .bpsk: return CYRINX_MODULATION_BPSK
        case .qpsk: return CYRINX_MODULATION_QPSK
        case .qam16: return CYRINX_MODULATION_16QAM
        case .qam64: return CYRINX_MODULATION_64QAM
        case .qam256: return CYRINX_MODULATION_256QAM
        }
    }

    public var bitsPerBin: Int {
        switch self {
        case .bpsk: return 1
        case .qpsk: return 2
        case .qam16: return 4
        case .qam64: return 6
        case .qam256: return 8
        }
    }
}

public enum CyrinxCodeRate: Sendable, Codable, Equatable, Hashable {
    case rate1_2
    case rate2_3
    case rate3_4
    case rate5_6

    public init?(_ cValue: cyrinx_code_rate_t) {
        switch cValue {
        case CYRINX_CODE_RATE_1_2: self = .rate1_2
        case CYRINX_CODE_RATE_2_3: self = .rate2_3
        case CYRINX_CODE_RATE_3_4: self = .rate3_4
        case CYRINX_CODE_RATE_5_6: self = .rate5_6
        default: return nil
        }
    }

    public var cValue: cyrinx_code_rate_t {
        switch self {
        case .rate1_2: return CYRINX_CODE_RATE_1_2
        case .rate2_3: return CYRINX_CODE_RATE_2_3
        case .rate3_4: return CYRINX_CODE_RATE_3_4
        case .rate5_6: return CYRINX_CODE_RATE_5_6
        }
    }

    public var stringValue: String {
        switch self {
        case .rate1_2: return "1/2"
        case .rate2_3: return "2/3"
        case .rate3_4: return "3/4"
        case .rate5_6: return "5/6"
        }
    }
}

public enum CyrinxClassification: Sendable, Codable, Equatable, Hashable {
    case compatibility
    case qualified
    case experimental
    case internalTestFixture

    public init?(_ cValue: cyrinx_classification_t) {
        switch cValue {
        case CYRINX_CLASSIFICATION_COMPATIBILITY: self = .compatibility
        case CYRINX_CLASSIFICATION_QUALIFIED: self = .qualified
        case CYRINX_CLASSIFICATION_EXPERIMENTAL: self = .experimental
        case CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE: self = .internalTestFixture
        default: return nil
        }
    }

    public var cValue: cyrinx_classification_t {
        switch self {
        case .compatibility: return CYRINX_CLASSIFICATION_COMPATIBILITY
        case .qualified: return CYRINX_CLASSIFICATION_QUALIFIED
        case .experimental: return CYRINX_CLASSIFICATION_EXPERIMENTAL
        case .internalTestFixture: return CYRINX_CLASSIFICATION_INTERNAL_TEST_FIXTURE
        }
    }
}

public struct CyrinxProfile: Sendable, Codable, Equatable, Hashable {
    public let id: UInt32
    public let classification: CyrinxClassification
    public let modulation: CyrinxModulation
    public let codeRate: CyrinxCodeRate

    public let lowFrequencyHz: Double
    public let highFrequencyHz: Double

    public let fftSize: UInt32
    public let cyclicPrefix: UInt32
    public let sampleRate: UInt32

    public let pilotEvery: UInt32
    public let symbolCount: UInt32
    public let blockCount: UInt32

    public let amplitude: Double
    public let clipSigma: Double
    public let chirpF0: Double
    public let chirpF1: Double

    public let hash: Data

    public init(
        id: UInt32,
        classification: CyrinxClassification,
        modulation: CyrinxModulation,
        codeRate: CyrinxCodeRate,
        lowFrequencyHz: Double,
        highFrequencyHz: Double,
        fftSize: UInt32,
        cyclicPrefix: UInt32,
        sampleRate: UInt32,
        pilotEvery: UInt32,
        symbolCount: UInt32,
        blockCount: UInt32,
        amplitude: Double,
        clipSigma: Double,
        chirpF0: Double,
        chirpF1: Double,
        hash: Data
    ) {
        self.id = id
        self.classification = classification
        self.modulation = modulation
        self.codeRate = codeRate
        self.lowFrequencyHz = lowFrequencyHz
        self.highFrequencyHz = highFrequencyHz
        self.fftSize = fftSize
        self.cyclicPrefix = cyclicPrefix
        self.sampleRate = sampleRate
        self.pilotEvery = pilotEvery
        self.symbolCount = symbolCount
        self.blockCount = blockCount
        self.amplitude = amplitude
        self.clipSigma = clipSigma
        self.chirpF0 = chirpF0
        self.chirpF1 = chirpF1
        self.hash = hash
    }

    public init?(_ raw: cyrinx_profile_t) {
        guard let classification = CyrinxClassification(cyrinx_classification_t(raw.classification)),
            let modulation = CyrinxModulation(cyrinx_modulation_t(raw.modulation)),
            let codeRate = CyrinxCodeRate(cyrinx_code_rate_t(raw.code_rate))
        else {
            return nil
        }

        self.id = raw.id
        self.classification = classification
        self.modulation = modulation
        self.codeRate = codeRate

        self.lowFrequencyHz = raw.low_frequency_hz
        self.highFrequencyHz = raw.high_frequency_hz

        self.fftSize = raw.fft_size
        self.cyclicPrefix = raw.cyclic_prefix
        self.sampleRate = raw.sample_rate

        self.pilotEvery = raw.pilot_every
        self.symbolCount = raw.symbol_count
        self.blockCount = raw.block_count

        self.amplitude = raw.amplitude
        self.clipSigma = raw.clip_sigma
        self.chirpF0 = raw.chirp_f0
        self.chirpF1 = raw.chirp_f1

        var hashBytes = [UInt8]()
        hashBytes.reserveCapacity(32)
        withUnsafeBytes(of: raw.hash) { ptr in
            hashBytes.append(contentsOf: ptr)
        }
        self.hash = Data(hashBytes)
    }

    public func toCStruct() -> cyrinx_profile_t {
        var raw = cyrinx_profile_t()
        raw.struct_size = MemoryLayout<cyrinx_profile_t>.size
        raw.abi_version = UInt32(CYRINX_PROFILE_ABI_VERSION)

        raw.id = self.id
        raw.classification = self.classification.cValue.rawValue
        raw.modulation = self.modulation.cValue.rawValue
        raw.code_rate = self.codeRate.cValue.rawValue

        raw.low_frequency_hz = self.lowFrequencyHz
        raw.high_frequency_hz = self.highFrequencyHz

        raw.fft_size = self.fftSize
        raw.cyclic_prefix = self.cyclicPrefix
        raw.sample_rate = self.sampleRate

        raw.pilot_every = self.pilotEvery
        raw.symbol_count = self.symbolCount
        raw.block_count = self.blockCount

        raw.amplitude = self.amplitude
        raw.clip_sigma = self.clipSigma
        raw.chirp_f0 = self.chirpF0
        raw.chirp_f1 = self.chirpF1

        var rawHash = raw.hash
        withUnsafeMutableBytes(of: &rawHash) { destPtr in
            self.hash.withUnsafeBytes { srcPtr in
                destPtr.copyBytes(from: srcPtr.prefix(32))
            }
        }
        raw.hash = rawHash

        return raw
    }

    public func toBulkConfiguration() -> BulkPHY.Configuration {
        BulkPHY.Configuration(
            lowFrequencyHz: self.lowFrequencyHz,
            highFrequencyHz: self.highFrequencyHz,
            pilotEvery: Int(self.pilotEvery),
            bitsPerBin: self.modulation.bitsPerBin,
            rate: self.codeRate.stringValue,
            symbolCount: Int(self.symbolCount),
            fftSize: Int(self.fftSize),
            cyclicPrefix: Int(self.cyclicPrefix),
            sampleRate: Int(self.sampleRate),
            amplitude: self.amplitude,
            clipSigma: self.clipSigma,
            chirpF0: self.chirpF0,
            chirpF1: self.chirpF1
        )
    }
}

public enum CyrinxProfileRegistry {
    /// Returns the total number of profiles in the registry.
    public static var count: Int {
        return Int(cyrinx_profile_get_count())
    }

    /// Retrieves a profile by its 0-based index.
    public static func get(index: Int) -> CyrinxProfile? {
        guard index >= 0 else { return nil }
        var raw = cyrinx_profile_t()
        raw.struct_size = MemoryLayout<cyrinx_profile_t>.size
        raw.abi_version = UInt32(CYRINX_PROFILE_ABI_VERSION)

        let status = cyrinx_profile_get_by_index(Int(index), &raw)
        guard status == 0 else { return nil }
        return CyrinxProfile(raw)
    }

    /// Retrieves a profile by its stable on-wire ID.
    public static func get(id: UInt32) -> CyrinxProfile? {
        var raw = cyrinx_profile_t()
        raw.struct_size = MemoryLayout<cyrinx_profile_t>.size
        raw.abi_version = UInt32(CYRINX_PROFILE_ABI_VERSION)

        let status = cyrinx_profile_get_by_id(id, &raw)
        guard status == 0 else { return nil }
        return CyrinxProfile(raw)
    }

    /// Validates a Swift-side profile using the C validation function.
    public static func validate(_ profile: CyrinxProfile) -> Bool {
        var raw = profile.toCStruct()
        return cyrinx_profile_validate(&raw) == 0
    }

    /// Computes the canonical hash of a Swift-side profile using the C hashing function.
    public static func computeHash(_ profile: CyrinxProfile) -> Data? {
        var raw = profile.toCStruct()
        var hashBytes = [UInt8](repeating: 0, count: 32)
        let status = cyrinx_profile_compute_hash(&raw, &hashBytes)
        guard status == 0 else { return nil }
        return Data(hashBytes)
    }
}
