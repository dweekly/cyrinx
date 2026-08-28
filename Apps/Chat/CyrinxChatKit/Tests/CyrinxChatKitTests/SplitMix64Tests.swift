import Testing

@testable import CyrinxChatKit

/// Sanity tests for the `SplitMix64` PRNG (CONTRACT.md §2, point 4): same
/// seed reproduces the same draw sequence; different seeds diverge; the
/// generator never gets "stuck" repeating a value across a short run.
@Suite("SplitMix64")
struct SplitMix64Tests {
    @Test("same seed reproduces the identical draw sequence")
    func sameSeedIsDeterministic() {
        var a = SplitMix64(seed: 0x1234_5678_9ABC_DEF0)
        var b = SplitMix64(seed: 0x1234_5678_9ABC_DEF0)
        let drawsA = (0..<8).map { _ in a.next() }
        let drawsB = (0..<8).map { _ in b.next() }
        #expect(drawsA == drawsB)
    }

    @Test("different seeds diverge on the first draw")
    func differentSeedsDiverge() {
        var a = SplitMix64(seed: 1)
        var b = SplitMix64(seed: 2)
        #expect(a.next() != b.next())
    }

    @Test("consecutive draws from one generator are pairwise distinct over a short run")
    func consecutiveDrawsAreDistinct() {
        var generator = SplitMix64(seed: 42)
        let draws = (0..<64).map { _ in generator.next() }
        #expect(Set(draws).count == draws.count)
    }

    @Test("big-endian byte decomposition round-trips through UInt64")
    func bigEndianBytesRoundTrip() {
        let value: UInt64 = 0x0102_0304_0506_0708
        let bytes = splitMix64BigEndianBytes(value)
        #expect(bytes == [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08])
    }
}
