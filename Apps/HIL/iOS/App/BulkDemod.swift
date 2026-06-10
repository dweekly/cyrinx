import Foundation

/// On-device demodulator for the wideband bulk OFDM PHY.
///
/// Faithful Swift port of the receive path of `scratch/hw20k/modem.py` (the
/// Python prototype validated over the air) and its Kotlin sibling
/// `Apps/HIL/android/.../BulkDemod.kt`. Decodes a PCM16LE capture from this
/// device's own microphone and verifies payload bytes against the DetRng
/// (splitmix64) PRBS the Mac transmitter uses, so the iPhone autonomously
/// proves end-to-end goodput without shipping samples off-device.
///
/// Fixed profile: 48 kHz, NFFT 2048, CP 768, comb pilots every 8th used bin,
/// uniform 16-QAM, convolutional K=7 (171,133) punctured to 3/4, frame-wide
/// interleaving, CRC32 per 256-byte payload block.
///
/// Verification is ORDERED (block j of a frame must equal payload position j of
/// the attributed payload), matching modem.py's `blocks` field semantics, not
/// the looser set-membership check the Kotlin port used.
enum BulkDemod {
    static let SRATE = 48_000
    static let NFFT = 2_048
    static let CP = 768
    static let SYM = NFFT + CP
    static let CHIRP_LEN = 4_096
    static let CHIRP_F0 = 2_000.0
    static let CHIRP_F1 = 16_000.0
    static let GUARD = 2_048
    static let CRC_BLOCK = 256
    static let PILOT_EVERY = 8
    static let BITS_PER_BIN = 4  // 16-QAM
    static let PUNCTURE = [1, 1, 0, 1, 1, 0]  // rate 3/4
    static let RATE = 0.75

    // ---------------- splitmix64 (must match modem.py DetRng) ----------------
    final class DetRng {
        private var s: UInt64
        init(_ seed: UInt64) { s = seed }
        func u64() -> UInt64 {
            s &+= 0x9E37_79B9_7F4A_7C15
            var z = s
            z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
            z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
            return z ^ (z >> 31)
        }

        func mod(_ m: Int) -> Int { Int(u64() % UInt64(m)) }
        func bits(_ n: Int) -> [UInt8] { (0..<n).map { _ in UInt8((u64() >> 63) & 1) } }
        func bytes(_ n: Int) -> [UInt8] { (0..<n).map { _ in UInt8((u64() >> 56) & 0xFF) } }
        func permutation(_ n: Int) -> [Int] {
            var p = Array(0..<n)
            var i = n - 1
            while i >= 1 {
                let j = mod(i + 1)
                p.swapAt(i, j)
                i -= 1
            }
            return p
        }
    }

    // ---------------- double-precision radix-2 FFT ----------------
    /// Matches modem.py's numpy FFT conventions: forward uses e^{-2pi i kn/N};
    /// inverse is unscaled here (callers divide by N to match irfft).
    /// Immutable after init (transform mutates only its arguments), so safe to
    /// share across threads.
    final class DFFT: @unchecked Sendable {
        let size: Int
        private let rev: [Int]
        private let cosT: [Double]
        private let sinT: [Double]
        init(_ size: Int) {
            self.size = size
            var log2 = 0
            var t = size
            while t > 1 {
                t >>= 1
                log2 += 1
            }
            var rv = [Int](repeating: 0, count: size)
            for i in 0..<size {
                var r = 0
                var v = i
                for _ in 0..<log2 {
                    r = (r << 1) | (v & 1)
                    v >>= 1
                }
                rv[i] = r
            }
            rev = rv
            var c = [Double](repeating: 0, count: size / 2)
            var sn = [Double](repeating: 0, count: size / 2)
            for k in 0..<(size / 2) {
                c[k] = cos(2.0 * Double.pi * Double(k) / Double(size))
                sn[k] = sin(2.0 * Double.pi * Double(k) / Double(size))
            }
            cosT = c
            sinT = sn
        }

        func transform(_ re: inout [Double], _ im: inout [Double], forward: Bool) {
            let n = size
            for i in 0..<n {
                let j = rev[i]
                if i < j {
                    re.swapAt(i, j)
                    im.swapAt(i, j)
                }
            }
            var len = 2
            while len <= n {
                let half = len / 2
                let step = n / len
                var base = 0
                while base < n {
                    var k = 0
                    for off in 0..<half {
                        let wr = cosT[k]
                        let wi = forward ? -sinT[k] : sinT[k]
                        let i0 = base + off
                        let i1 = i0 + half
                        let tr = re[i1] * wr - im[i1] * wi
                        let ti = re[i1] * wi + im[i1] * wr
                        re[i1] = re[i0] - tr
                        im[i1] = im[i0] - ti
                        re[i0] += tr
                        im[i0] += ti
                        k += step
                    }
                    base += len
                }
                len <<= 1
            }
        }
    }

    nonisolated(unsafe) private static let fft2048 = DFFT(NFFT)

    // ---------------- config ----------------
    final class Cfg {
        let binHz: Double
        let binLo: Int
        let binHi: Int
        let used: [Int]
        let pilotPos: [Int]  // indices into `used` that are pilots
        let dataPos: [Int]
        let pilotsRe: [Double]
        let pilotsIm: [Double]
        let bitsPerSym: Int
        let infoBits: Int
        let nBlocks: Int
        let payloadBytes: Int
        let frameSamples: Int
        let nSym: Int

        init(fLo: Double, fHi: Double, nSym: Int) {
            self.nSym = nSym
            binHz = Double(SRATE) / Double(NFFT)
            binLo = Int(ceil(fLo / binHz))
            binHi = Int(floor(fHi / binHz))
            used = Array(binLo...binHi)
            var pp = [Int]()
            var dp = [Int]()
            for i in used.indices {
                if i % PILOT_EVERY == 0 { pp.append(i) } else { dp.append(i) }
            }
            pilotPos = pp
            dataPos = dp
            let r = DetRng(0xBEEF)
            var pre = [Double](repeating: 0, count: pp.count)
            var pim = [Double](repeating: 0, count: pp.count)
            for i in pp.indices {
                let ph = Double.pi / 4 + Double.pi / 2 * Double(r.mod(4))
                pre[i] = cos(ph)
                pim[i] = sin(ph)
            }
            pilotsRe = pre
            pilotsIm = pim
            bitsPerSym = dp.count * BITS_PER_BIN
            let coded = bitsPerSym * nSym
            infoBits = Int(floor(Double(coded) * RATE)) - 6
            nBlocks = infoBits / ((CRC_BLOCK + 4) * 8)
            payloadBytes = nBlocks * CRC_BLOCK
            frameSamples = CHIRP_LEN + GUARD + (2 + nSym) * SYM
        }
    }

    static func syncSymbolFreq(_ cfg: Cfg, _ which: Int) -> ([Double], [Double]) {
        let r = DetRng(UInt64(0x5EED + which))
        var re = [Double](repeating: 0, count: cfg.used.count)
        var im = [Double](repeating: 0, count: cfg.used.count)
        for i in cfg.used.indices {
            let ph = Double.pi / 4 + Double.pi / 2 * Double(r.mod(4))
            re[i] = cos(ph)
            im[i] = sin(ph)
        }
        return (re, im)
    }

    /// time-domain OFDM symbol (CP + body) from freq values on used bins,
    /// matching numpy irfft conventions (scale 1/N, Hermitian symmetry).
    static func ofdmModSymbol(_ cfg: Cfg, _ fre: [Double], _ fim: [Double]) -> [Double] {
        var re = [Double](repeating: 0, count: NFFT)
        var im = [Double](repeating: 0, count: NFFT)
        for i in cfg.used.indices {
            let b = cfg.used[i]
            re[b] = fre[i]
            im[b] = fim[i]
            re[NFFT - b] = fre[i]
            im[NFFT - b] = -fim[i]
        }
        fft2048.transform(&re, &im, forward: false)  // inverse, unscaled
        var out = [Double](repeating: 0, count: SYM)
        for i in 0..<NFFT { re[i] /= Double(NFFT) }
        for i in 0..<CP { out[i] = re[NFFT - CP + i] }
        for i in 0..<NFFT { out[CP + i] = re[i] }
        return out
    }

    static func chirp() -> [Double] {
        var w = [Double](repeating: 0, count: CHIRP_LEN)
        let tTot = Double(CHIRP_LEN) / Double(SRATE)
        for i in 0..<CHIRP_LEN {
            let t = Double(i) / Double(SRATE)
            let ph = 2.0 * Double.pi * (CHIRP_F0 * t + 0.5 * (CHIRP_F1 - CHIRP_F0) * t * t / tTot)
            w[i] = sin(ph)
        }
        let r = 128
        for i in 0..<r {
            let env = 0.5 - 0.5 * cos(Double.pi * Double(i) / Double(r))
            w[i] *= env
            w[CHIRP_LEN - 1 - i] *= env
        }
        return w
    }

    /// matched filter |xcorr| via overlap-save; returns magnitude array sized len(x)-CHIRP_LEN+1
    static func chirpMatchedFilter(_ x: [Double]) -> [Double] {
        let n = 16_384
        let hop = n - CHIRP_LEN
        let f = DFFT(n)
        var cRe = [Double](repeating: 0, count: n)
        var cIm = [Double](repeating: 0, count: n)
        let ch = chirp()
        // correlation = convolution with reversed chirp
        for i in 0..<CHIRP_LEN { cRe[i] = ch[CHIRP_LEN - 1 - i] }
        f.transform(&cRe, &cIm, forward: true)
        let outLen = x.count - CHIRP_LEN + 1
        if outLen <= 0 { return [] }
        var out = [Double](repeating: 0, count: outLen)
        var pos = 0
        var bRe = [Double](repeating: 0, count: n)
        var bIm = [Double](repeating: 0, count: n)
        while pos < outLen {
            for i in 0..<n {
                bRe[i] = 0
                bIm[i] = 0
            }
            let m = min(n, x.count - pos)
            for i in 0..<m { bRe[i] = x[pos + i] }
            f.transform(&bRe, &bIm, forward: true)
            for i in 0..<n {
                let rr = bRe[i] * cRe[i] - bIm[i] * cIm[i]
                let ii = bRe[i] * cIm[i] + bIm[i] * cRe[i]
                bRe[i] = rr
                bIm[i] = ii
            }
            f.transform(&bRe, &bIm, forward: false)
            let lim = min(hop, outLen - pos)
            for i in 0..<lim {
                let k = i + CHIRP_LEN - 1
                out[pos + i] = sqrt(bRe[k] * bRe[k] + bIm[k] * bIm[k]) / Double(n)
            }
            pos += hop
        }
        return out
    }

    static func findAllChirps(_ x: [Double], frameSamples: Int, maxFrames: Int) -> [Int] {
        var mf = chirpMatchedFilter(x)
        if mf.isEmpty { return [] }
        var mx = 0.0
        for v in mf where v > mx { mx = v }
        let thr = mx * 0.4
        var peaks = [Int]()
        for _ in 0..<(maxFrames + 4) {
            var k = 0
            var best = 0.0
            for i in mf.indices where mf[i] > best {
                best = mf[i]
                k = i
            }
            if best < thr { break }
            peaks.append(k)
            let lo = max(0, k - frameSamples / 2)
            let hi = min(mf.count, k + frameSamples / 2)
            for i in lo..<hi { mf[i] = 0 }
        }
        peaks.sort()
        return peaks
    }

    // ---------------- Viterbi (K=7, 171/133) ----------------
    private static let NEXT: [[Int]] = (0..<64).map { s in (0..<2).map { b in ((b << 6) | s) >> 1 } }
    private static let OUT0: [[Int]] = (0..<64).map { s in
        (0..<2).map { b in (((b << 6) | s) & 0x79).nonzeroBitCount & 1 }
    }  // 0o171
    private static let OUT1: [[Int]] = (0..<64).map { s in
        (0..<2).map { b in (((b << 6) | s) & 0x5B).nonzeroBitCount & 1 }
    }  // 0o133

    static func viterbi(_ llr0: [Double], _ llr1: [Double], _ nInfo: Int) -> [UInt8] {
        let n = llr0.count
        var metrics = [Double](repeating: -1e12, count: 64)
        metrics[0] = 0.0
        var back = [[UInt8]](repeating: [UInt8](repeating: 0, count: 64), count: n)
        var newM = [Double](repeating: 0, count: 64)
        for i in 0..<n {
            let l0 = llr0[i]
            let l1 = llr1[i]
            for k in 0..<64 { newM[k] = -1e18 }
            for s in 0..<64 {
                let ms = metrics[s]
                if ms < -1e11 { continue }
                for b in 0..<2 {
                    let bm =
                        (OUT0[s][b] == 0 ? 0.5 * l0 : -0.5 * l0) + (OUT1[s][b] == 0 ? 0.5 * l1 : -0.5 * l1)
                    let cand = ms + bm
                    let nx = NEXT[s][b]
                    if cand > newM[nx] {
                        newM[nx] = cand
                        back[i][nx] = UInt8((s << 1) | b)
                    }
                }
            }
            swap(&metrics, &newM)
        }
        var s = 0
        var bits = [UInt8](repeating: 0, count: n)
        var i = n - 1
        while i >= 0 {
            let o = Int(back[i][s]) & 0x7F
            bits[i] = UInt8(o & 1)
            s = o >> 1
            i -= 1
        }
        return Array(bits.prefix(nInfo))
    }

    // ---------------- 16-QAM max-log LLR (Gray, matches qam_llr) ----------------
    // axis levels /sqrt(10): -3,-1,1,3 ; gray order from _gray_levels(2):
    // gray codes of 0..3 = [0,1,3,2]; order = argsort = [0,1,3,2]
    private static let LV: [Double] = [-3.0, -1.0, 1.0, 3.0].map { $0 / sqrt(10.0) }
    private static let GOFPOS: [Int] = [0, 1, 3, 2]  // gray index g of level position p

    private static func axisLlr(_ y: Double, _ n0: Double, _ out: inout [Double], _ off: Int) {
        var d0b0 = 1e18, d1b0 = 1e18, d0b1 = 1e18, d1b1 = 1e18
        for p in 0..<4 {
            let d = (y - LV[p]) * (y - LV[p])
            let g = GOFPOS[p]
            if (g >> 1) & 1 == 1 { if d < d1b0 { d1b0 = d } } else if d < d0b0 { d0b0 = d }
            if g & 1 == 1 { if d < d1b1 { d1b1 = d } } else if d < d0b1 { d0b1 = d }
        }
        out[off] = (d1b0 - d0b0) / n0
        out[off + 1] = (d1b1 - d0b1) / n0
    }

    // ---------------- main entry ----------------
    struct FrameResult {
        let start: Int
        let ok: Bool
        let err: String?
        let blocksOk: Int
        let blocksTotal: Int
        let verified: Int
        let evm: Double
        let decodeMs: Int
        /// per-block (index, crcOk) so callers can map ordered positions
        let blockCrcOk: [Bool]
        let blockBytes: [[UInt8]?]
    }

    static func decodeCapture(
        path: String, channels: Int, fLo: Double, fHi: Double,
        nSym: Int, nPayloads: Int, payloadSeedBase: UInt64,
        log: (String) -> Void
    ) -> (frames: [FrameResult], verified: Int, spanSamples: Int) {
        let cfg = Cfg(fLo: fLo, fHi: fHi, nSym: nSym)
        guard let data = FileManager.default.contents(atPath: path) else {
            log("bulk_decode: cannot read \(path)")
            return ([], 0, 0)
        }
        let nFrames = data.count / 2 / channels
        var x = [Double](repeating: 0, count: nFrames)
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let p = raw.bindMemory(to: Int16.self)
            for i in 0..<nFrames {
                x[i] = Double(Int16(littleEndian: p[i * channels])) / 32768.0
            }
        }
        log(
            String(
                format:
                    "bulk_decode: %d samples (%.1fs) band %d-%d nSym=%d blocks=%d payload=%dB dataBins=%d",
                x.count, Double(x.count) / Double(SRATE), Int(fLo), Int(fHi), nSym,
                cfg.nBlocks, cfg.payloadBytes, cfg.dataPos.count))

        // Ordered expected payloads: payload p has blocks at fixed positions.
        // We try to attribute each captured frame to the payload index whose
        // ordered blocks it best matches, then count blocks that are BOTH
        // CRC-ok AND byte-equal at the SAME ordered position.
        var expectedPayloads = [[[UInt8]]]()
        for i in 0..<nPayloads {
            let pl = DetRng(payloadSeedBase + UInt64(i)).bytes(cfg.payloadBytes)
            var blocks = [[UInt8]]()
            for j in 0..<cfg.nBlocks {
                blocks.append(Array(pl[(j * CRC_BLOCK)..<((j + 1) * CRC_BLOCK)]))
            }
            expectedPayloads.append(blocks)
        }

        let starts = findAllChirps(x, frameSamples: cfg.frameSamples, maxFrames: nPayloads)
        log("bulk_decode: \(starts.count) chirp(s) found")
        var results = [FrameResult]()
        var totalVerified = 0
        var usedPayloadIdx = Set<Int>()
        for st in starts {
            let t0 = Date()
            var r = demodFrame(cfg, x, chirpStart: st)
            let ms = Int(Date().timeIntervalSince(t0) * 1000)
            // Ordered attribution: pick the as-yet-unclaimed payload whose
            // ordered blocks maximize byte-equal matches against CRC-ok blocks.
            var bestIdx = -1
            var bestMatch = -1
            if r.ok {
                for pi in 0..<nPayloads where !usedPayloadIdx.contains(pi) {
                    var m = 0
                    for j in 0..<cfg.nBlocks where r.blockCrcOk[j] {
                        if let got = r.blockBytes[j], got == expectedPayloads[pi][j] { m += 1 }
                    }
                    if m > bestMatch {
                        bestMatch = m
                        bestIdx = pi
                    }
                }
            }
            var verified = 0
            if bestIdx >= 0, bestMatch > 0 {
                usedPayloadIdx.insert(bestIdx)
                verified = bestMatch
            }
            totalVerified += verified
            r = FrameResult(
                start: r.start, ok: r.ok, err: r.err, blocksOk: r.blocksOk,
                blocksTotal: r.blocksTotal, verified: verified, evm: r.evm,
                decodeMs: ms, blockCrcOk: r.blockCrcOk, blockBytes: r.blockBytes)
            results.append(r)
            if r.ok {
                log(
                    String(
                        format:
                            "bulk_decode frame@%.2fs: blocks=%d/%d verified=%d (payload#%d) evm=%.3f decode_ms=%d",
                        Double(st) / Double(SRATE), r.blocksOk, r.blocksTotal, verified, bestIdx, r.evm, ms))
            } else {
                log(
                    "bulk_decode frame@\(String(format: "%.2f", Double(st) / Double(SRATE)))s: FAILED \(r.err ?? "?")"
                )
            }
        }
        let okFrames = results.filter { $0.ok && $0.verified > 0 }
        var span = 0
        if !okFrames.isEmpty {
            let first = okFrames.map(\.start).min()!
            let last = okFrames.map(\.start).max()!
            span = last + cfg.frameSamples - first
        }
        return (results, totalVerified, span)
    }

    private static func fftUsed(_ cfg: Cfg, _ x: [Double], _ pos: Int) -> ([Double], [Double])? {
        if pos + CP + NFFT > x.count || pos < 0 { return nil }
        var re = [Double](repeating: 0, count: NFFT)
        var im = [Double](repeating: 0, count: NFFT)
        for i in 0..<NFFT { re[i] = x[pos + CP + i] }
        fft2048.transform(&re, &im, forward: true)
        var ur = [Double](repeating: 0, count: cfg.used.count)
        var ui = [Double](repeating: 0, count: cfg.used.count)
        for i in cfg.used.indices {
            ur[i] = re[cfg.used[i]]
            ui[i] = im[cfg.used[i]]
        }
        return (ur, ui)
    }

    private static func demodFrame(_ cfg: Cfg, _ x: [Double], chirpStart: Int) -> FrameResult {
        var base = chirpStart + CHIRP_LEN + GUARD
        // fine sync via xcorr with sync symbol 0 reference
        let (s0re, s0im) = syncSymbolFreq(cfg, 0)
        let ref = ofdmModSymbol(cfg, s0re, s0im)
        let lo = max(0, base - 400)
        var bestOff = 0
        var bestV = -1.0
        for off in 0...800 {
            let p = lo + off
            if p + SYM > x.count { break }
            var acc = 0.0
            for i in 0..<SYM { acc += x[p + i] * ref[i] }
            if abs(acc) > bestV {
                bestV = abs(acc)
                bestOff = off
            }
        }
        base = lo + bestOff - 24  // early bias: pre-cursors stay inside CP

        let (s1re, s1im) = syncSymbolFreq(cfg, 1)
        guard let y0 = fftUsed(cfg, x, base) else { return fail(chirpStart, "short capture sync0", cfg) }
        guard let y1 = fftUsed(cfg, x, base + SYM) else {
            return fail(chirpStart, "short capture sync1", cfg)
        }
        let nb = cfg.used.count
        var h0r = [Double](repeating: 0, count: nb), h0i = [Double](repeating: 0, count: nb)
        var h1r = [Double](repeating: 0, count: nb), h1i = [Double](repeating: 0, count: nb)
        for i in 0..<nb {
            // H = Y / X with |X|=1: Y * conj(X)
            h0r[i] = y0.0[i] * s0re[i] + y0.1[i] * s0im[i]
            h0i[i] = y0.1[i] * s0re[i] - y0.0[i] * s0im[i]
            h1r[i] = y1.0[i] * s1re[i] + y1.1[i] * s1im[i]
            h1i[i] = y1.1[i] * s1re[i] - y1.0[i] * s1im[i]
        }
        var hr = [Double](repeating: 0, count: nb), hi = [Double](repeating: 0, count: nb)
        var nv = [Double](repeating: 0, count: nb)
        for i in 0..<nb {
            hr[i] = (h0r[i] + h1r[i]) / 2
            hi[i] = (h0i[i] + h1i[i]) / 2
            let dr = h0r[i] - h1r[i]
            let di = h0i[i] - h1i[i]
            nv[i] = (dr * dr + di * di) / 2
        }
        var nvs = [Double](repeating: 0, count: nb)
        for i in 0..<nb {
            var acc = 0.0
            var c = 0
            for j in max(0, i - 4)...min(nb - 1, i + 4) {
                acc += nv[j]
                c += 1
            }
            nvs[i] = acc / Double(c) + 1e-12
        }
        var snr = [Double](repeating: 0, count: nb)
        for i in 0..<nb { snr[i] = (hr[i] * hr[i] + hi[i] * hi[i]) / nvs[i] }

        let cap = cfg.bitsPerSym * cfg.nSym
        var llrStream = [Double](repeating: 0, count: cap)
        var pos = 0
        var evmAcc = 0.0
        let pn = cfg.pilotPos.count
        let pilBin0 = Double(cfg.used[cfg.pilotPos[0]])
        let pilStep = Double(cfg.used[cfg.pilotPos[1]] - cfg.used[cfg.pilotPos[0]])
        for s in 0..<cfg.nSym {
            guard let y = fftUsed(cfg, x, base + (2 + s) * SYM) else {
                return fail(chirpStart, "short capture at sym \(s)", cfg)
            }
            var zr = [Double](repeating: 0, count: nb), zi = [Double](repeating: 0, count: nb)
            for i in 0..<nb {
                let den = hr[i] * hr[i] + hi[i] * hi[i] + 1e-15
                zr[i] = (y.0[i] * hr[i] + y.1[i] * hi[i]) / den
                zi[i] = (y.1[i] * hr[i] - y.0[i] * hi[i]) / den
            }
            // pilot iterative slope + CPE fit
            var er = [Double](repeating: 0, count: pn), ei = [Double](repeating: 0, count: pn)
            for k in 0..<pn {
                let p = cfg.pilotPos[k]
                er[k] = zr[p] * cfg.pilotsRe[k] + zi[p] * cfg.pilotsIm[k]
                ei[k] = zi[p] * cfg.pilotsRe[k] - zr[p] * cfg.pilotsIm[k]
            }
            var slopeTot = 0.0
            for _ in 0..<3 {
                var dr = 0.0, di = 0.0
                for k in 1..<pn {
                    dr += er[k] * er[k - 1] + ei[k] * ei[k - 1]
                    di += ei[k] * er[k - 1] - er[k] * ei[k - 1]
                }
                let slope = atan2(di, dr) / pilStep
                slopeTot += slope
                for k in 0..<pn {
                    let binOff = Double(cfg.used[cfg.pilotPos[k]]) - pilBin0
                    let c = cos(slope * binOff)
                    let sn = sin(slope * binOff)
                    let nr = er[k] * c + ei[k] * sn
                    let ni = ei[k] * c - er[k] * sn
                    er[k] = nr
                    ei[k] = ni
                }
            }
            var sr = 0.0, si = 0.0
            for k in 0..<pn {
                sr += er[k]
                si += ei[k]
            }
            let ph0 = atan2(si, sr)
            for i in 0..<nb {
                let ang = ph0 + slopeTot * (Double(cfg.used[i]) - pilBin0)
                let c = cos(ang)
                let sn = sin(ang)
                let nr = zr[i] * c + zi[i] * sn
                let ni = zi[i] * c - zr[i] * sn
                zr[i] = nr
                zi[i] = ni
            }
            var evm2 = 0.0
            for k in 0..<pn {
                let p = cfg.pilotPos[k]
                let pr = zr[p] * cfg.pilotsRe[k] + zi[p] * cfg.pilotsIm[k]
                let pi = zi[p] * cfg.pilotsRe[k] - zr[p] * cfg.pilotsIm[k]
                evm2 += (pr - 1) * (pr - 1) + pi * pi
            }
            evm2 /= Double(pn)
            evmAcc += sqrt(evm2)
            for d in cfg.dataPos {
                let n0 = 1.0 / max(snr[d], 0.1) + evm2
                axisLlr(zr[d], n0, &llrStream, pos)
                axisLlr(zi[d], n0, &llrStream, pos + 2)
                pos += 4
            }
        }

        // deinterleave
        let perm = DetRng(0x1EAF).permutation(cap)
        var llr = [Double](repeating: 0, count: cap)
        for i in 0..<cap { llr[i] = llrStream[perm[i]] }
        // depuncture to full rate-1/2 stream
        let nCodedFull = (cfg.infoBits + 6) * 2
        var full = [Double](repeating: 0, count: nCodedFull)
        var li = 0
        for i in 0..<nCodedFull where PUNCTURE[i % PUNCTURE.count] == 1 {
            full[i] = llr[li]
            li += 1
        }
        var llr0 = [Double](repeating: 0, count: nCodedFull / 2)
        var llr1 = [Double](repeating: 0, count: nCodedFull / 2)
        for i in llr0.indices {
            llr0[i] = full[2 * i]
            llr1[i] = full[2 * i + 1]
        }
        let bits = viterbi(llr0, llr1, cfg.infoBits)

        // pack bits -> bytes; CRC per block; record ordered content
        var streamBytes = [UInt8](repeating: 0, count: cfg.nBlocks * (CRC_BLOCK + 4))
        for i in streamBytes.indices {
            var v = 0
            for b in 0..<8 { v = (v << 1) | Int(bits[i * 8 + b]) }
            streamBytes[i] = UInt8(v & 0xFF)
        }
        var ok = 0
        var crcOk = [Bool](repeating: false, count: cfg.nBlocks)
        var blkBytes = [[UInt8]?](repeating: nil, count: cfg.nBlocks)
        for j in 0..<cfg.nBlocks {
            let off = j * (CRC_BLOCK + 4)
            let blk = Array(streamBytes[off..<(off + CRC_BLOCK)])
            let computed = crc32(blk)
            let want =
                (UInt32(streamBytes[off + CRC_BLOCK]) << 24)
                | (UInt32(streamBytes[off + CRC_BLOCK + 1]) << 16)
                | (UInt32(streamBytes[off + CRC_BLOCK + 2]) << 8) | UInt32(streamBytes[off + CRC_BLOCK + 3])
            if computed == want {
                ok += 1
                crcOk[j] = true
                blkBytes[j] = blk
            }
        }
        return FrameResult(
            start: chirpStart, ok: true, err: nil, blocksOk: ok,
            blocksTotal: cfg.nBlocks, verified: 0, evm: evmAcc / Double(cfg.nSym),
            decodeMs: 0, blockCrcOk: crcOk, blockBytes: blkBytes)
    }

    private static func fail(_ start: Int, _ err: String, _ cfg: Cfg) -> FrameResult {
        FrameResult(
            start: start, ok: false, err: err, blocksOk: 0, blocksTotal: cfg.nBlocks,
            verified: 0, evm: 0, decodeMs: 0,
            blockCrcOk: [Bool](repeating: false, count: cfg.nBlocks),
            blockBytes: [[UInt8]?](repeating: nil, count: cfg.nBlocks))
    }

    // ---------------- CRC32 (zlib/IEEE, matches Python zlib.crc32) ----------------
    private static let crcTable: [UInt32] = {
        (0..<256).map { (i: Int) -> UInt32 in
            var c = UInt32(i)
            for _ in 0..<8 { c = (c & 1) != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1 }
            return c
        }
    }()

    static func crc32(_ bytes: [UInt8]) -> UInt32 {
        var c: UInt32 = 0xFFFF_FFFF
        for b in bytes {
            c = crcTable[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8)
        }
        return c ^ 0xFFFF_FFFF
    }
}
