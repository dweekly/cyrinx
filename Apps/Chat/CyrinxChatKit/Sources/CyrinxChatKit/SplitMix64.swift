/// Deterministic seeded PRNG for `SimulatedChatTransportClient`.
/// Apps/Chat/CONTRACT.md §2, point 4.
///
/// Public-domain reference algorithm: Vigna, `splitmix64.c`,
/// <http://prng.di.unimi.it/splitmix64.c>; also the generator described in
/// Steele, Lea & Flood, "Fast Splittable Pseudorandom Number Generators,"
/// OOPSLA 2014. Constants and the `next()` step below are transcribed
/// verbatim from CONTRACT.md §2, which itself transcribes them from the
/// cited reference -- they are not free parameters and must not be
/// "improved" independently of a spec change.
public struct SplitMix64: Sendable {
    /// Per-call state increment (CONTRACT.md §2).
    static let goldenGamma: UInt64 = 0x9E37_79B9_7F4A_7C15
    /// First avalanche multiplier (CONTRACT.md §2).
    static let mixMul1: UInt64 = 0xBF58_476D_1CE4_E5B9
    /// Second avalanche multiplier (CONTRACT.md §2).
    static let mixMul2: UInt64 = 0x94D0_49BB_1331_11EB

    private var state: UInt64

    /// `state` is initialized directly from `seed`, no additional hashing
    /// of the seed itself (CONTRACT.md §2).
    public init(seed: UInt64) {
        self.state = seed
    }

    /// One u64 draw. Reference step, identical on both platforms
    /// (CONTRACT.md §2):
    /// ```
    /// state = state + GOLDEN_GAMMA            // (mod 2^64)
    /// z = state
    /// z = (z ^ (z >> 30)) * MIX_MUL_1         // (mod 2^64)
    /// z = (z ^ (z >> 27)) * MIX_MUL_2         // (mod 2^64)
    /// z = z ^ (z >> 31)
    /// return z
    /// ```
    public mutating func next() -> UInt64 {
        state = state &+ Self.goldenGamma
        var z = state
        z = (z ^ (z >> 30)) &* Self.mixMul1
        z = (z ^ (z >> 27)) &* Self.mixMul2
        z = z ^ (z >> 31)
        return z
    }
}

/// Big-endian byte decomposition of a `UInt64`, most-significant byte
/// first. Used by the simulator's PRNG draw-order contract (CONTRACT.md §2:
/// "Draw 1 -> 8 bytes (big-endian) of the u64 result, of which the first 4
/// bytes become client A's simulated local peer ID").
func splitMix64BigEndianBytes(_ value: UInt64) -> [UInt8] {
    (0..<8).map { index in
        UInt8((value >> (56 - 8 * UInt64(index))) & 0xFF)
    }
}
