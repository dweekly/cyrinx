package com.dweekly.cyrinxhil

import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.CRC32
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Legacy on-device control demodulator for the wideband bulk OFDM PHY.
 *
 * This older Kotlin port decodes a PCM16LE capture from this device's own
 * microphone and verifies payload bytes against the DetRng (splitmix64) PRBS
 * used by the Mac transmitter. It provides on-device evidence for its fixed
 * control profile without shipping samples off-device, but it is not the
 * canonical Cyrinx 2.0 receiver.
 *
 * Fixed Cyrinx 1.x control profile: 48 kHz, NFFT 2048, CP 768, comb pilots
 * every 8th used bin, uniform 16-QAM, convolutional K=7 (171,133) punctured
 * to 3/4, frame-wide interleaving, CRC32 per 256-byte payload block. It does
 * not implement CP96, pilot spacing 16 or 64, 64-QAM, rate 2/3, pilot-local
 * LLR reliability,
 * automatic microphone selection, or two-microphone MRC. Cyrinx 2.0 Pixel
 * campaigns use Android for tokenized stereo capture and a frozen build of the
 * canonical C library on the host for decoding; they are not on-device Kotlin
 * decode results.
 */
object BulkDemod {
    const val SRATE = 48000
    const val NFFT = 2048
    const val CP = 768
    const val SYM = NFFT + CP
    const val CHIRP_LEN = 4096
    const val CHIRP_F0 = 2000.0
    const val CHIRP_F1 = 16000.0
    const val GUARD = 2048
    const val CRC_BLOCK = 256
    const val PILOT_EVERY = 8
    const val BITS_PER_BIN = 4              // 16-QAM
    val PUNCTURE = intArrayOf(1, 1, 0, 1, 1, 0)   // rate 3/4
    const val RATE = 0.75

    // ---------------- splitmix64 (must match modem.py DetRng) ----------------
    class DetRng(seed: Long) {
        private var s: ULong = seed.toULong()
        fun u64(): ULong {
            s += 0x9E3779B97F4A7C15UL
            var z = s
            z = (z xor (z shr 30)) * 0xBF58476D1CE4E5B9UL
            z = (z xor (z shr 27)) * 0x94D049BB133111EBUL
            return z xor (z shr 31)
        }
        fun mod(m: Int): Int = (u64() % m.toULong()).toInt()
        fun bits(n: Int): ByteArray = ByteArray(n) { ((u64() shr 63).toInt() and 1).toByte() }
        fun bytes(n: Int): ByteArray = ByteArray(n) { ((u64() shr 56) and 0xFFUL).toInt().toByte() }
        fun permutation(n: Int): IntArray {
            val p = IntArray(n) { it }
            for (i in n - 1 downTo 1) {
                val j = mod(i + 1)
                val t = p[i]; p[i] = p[j]; p[j] = t
            }
            return p
        }
    }

    // ---------------- double-precision radix-2 FFT ----------------
    class DFFT(val size: Int) {
        private val rev = IntArray(size)
        private val cosT = DoubleArray(size / 2)
        private val sinT = DoubleArray(size / 2)
        init {
            var log2 = 0
            var t = size
            while (t > 1) { t = t shr 1; log2++ }
            for (i in 0 until size) {
                var r = 0
                var v = i
                for (j in 0 until log2) { r = (r shl 1) or (v and 1); v = v shr 1 }
                rev[i] = r
            }
            for (k in 0 until size / 2) {
                cosT[k] = cos(2.0 * PI * k / size)
                sinT[k] = sin(2.0 * PI * k / size)
            }
        }
        /** forward: X[k] = sum x[n] e^{-2pi i kn/N}; inverse: no 1/N scaling here */
        fun transform(re: DoubleArray, im: DoubleArray, forward: Boolean) {
            val n = size
            for (i in 0 until n) {
                val j = rev[i]
                if (i < j) {
                    var t = re[i]; re[i] = re[j]; re[j] = t
                    t = im[i]; im[i] = im[j]; im[j] = t
                }
            }
            var len = 2
            while (len <= n) {
                val half = len / 2
                val step = n / len
                for (base in 0 until n step len) {
                    var k = 0
                    for (off in 0 until half) {
                        val wr = cosT[k]
                        val wi = if (forward) -sinT[k] else sinT[k]
                        val i0 = base + off
                        val i1 = i0 + half
                        val tr = re[i1] * wr - im[i1] * wi
                        val ti = re[i1] * wi + im[i1] * wr
                        re[i1] = re[i0] - tr
                        im[i1] = im[i0] - ti
                        re[i0] += tr
                        im[i0] += ti
                        k += step
                    }
                }
                len = len shl 1
            }
        }
    }

    private val fft2048 = DFFT(NFFT)

    // ---------------- config ----------------
    class Cfg(fLo: Double, fHi: Double, val nSym: Int) {
        val binHz = SRATE.toDouble() / NFFT
        val binLo = kotlin.math.ceil(fLo / binHz).toInt()
        val binHi = floor(fHi / binHz).toInt()
        val used = IntArray(binHi - binLo + 1) { binLo + it }
        val pilotPos: IntArray   // indices into `used` that are pilots
        val dataPos: IntArray
        val pilotsRe: DoubleArray
        val pilotsIm: DoubleArray
        val bitsPerSym: Int
        val infoBits: Int
        val nBlocks: Int
        val payloadBytes: Int
        val frameSamples: Int
        init {
            val pp = ArrayList<Int>()
            val dp = ArrayList<Int>()
            for (i in used.indices) {
                if (i % PILOT_EVERY == 0) pp.add(i) else dp.add(i)
            }
            pilotPos = pp.toIntArray()
            dataPos = dp.toIntArray()
            val r = DetRng(0xBEEF)
            pilotsRe = DoubleArray(pilotPos.size)
            pilotsIm = DoubleArray(pilotPos.size)
            for (i in pilotPos.indices) {
                val ph = PI / 4 + PI / 2 * r.mod(4)
                pilotsRe[i] = cos(ph)
                pilotsIm[i] = sin(ph)
            }
            bitsPerSym = dataPos.size * BITS_PER_BIN
            val coded = bitsPerSym * nSym
            infoBits = floor(coded * RATE).toInt() - 6
            nBlocks = infoBits / ((CRC_BLOCK + 4) * 8)
            payloadBytes = nBlocks * CRC_BLOCK
            frameSamples = CHIRP_LEN + GUARD + (2 + nSym) * SYM
        }
    }

    fun syncSymbolFreq(cfg: Cfg, which: Int): Pair<DoubleArray, DoubleArray> {
        val r = DetRng(0x5EED + which.toLong())
        val re = DoubleArray(cfg.used.size)
        val im = DoubleArray(cfg.used.size)
        for (i in cfg.used.indices) {
            val ph = PI / 4 + PI / 2 * r.mod(4)
            re[i] = cos(ph)
            im[i] = sin(ph)
        }
        return Pair(re, im)
    }

    /** time-domain OFDM symbol (CP + body) from freq values on used bins,
     * matching numpy irfft conventions (scale 1/N, Hermitian symmetry). */
    fun ofdmModSymbol(cfg: Cfg, fre: DoubleArray, fim: DoubleArray): DoubleArray {
        val re = DoubleArray(NFFT)
        val im = DoubleArray(NFFT)
        for (i in cfg.used.indices) {
            val b = cfg.used[i]
            re[b] = fre[i]; im[b] = fim[i]
            re[NFFT - b] = fre[i]; im[NFFT - b] = -fim[i]
        }
        fft2048.transform(re, im, false)   // inverse, unscaled
        val out = DoubleArray(SYM)
        for (i in 0 until NFFT) re[i] /= NFFT
        for (i in 0 until CP) out[i] = re[NFFT - CP + i]
        for (i in 0 until NFFT) out[CP + i] = re[i]
        return out
    }

    fun chirp(): DoubleArray {
        val w = DoubleArray(CHIRP_LEN)
        val tTot = CHIRP_LEN.toDouble() / SRATE
        for (i in 0 until CHIRP_LEN) {
            val t = i.toDouble() / SRATE
            val ph = 2.0 * PI * (CHIRP_F0 * t + 0.5 * (CHIRP_F1 - CHIRP_F0) * t * t / tTot)
            w[i] = sin(ph)
        }
        val r = 128
        for (i in 0 until r) {
            val env = 0.5 - 0.5 * cos(PI * i / r)
            w[i] *= env
            w[CHIRP_LEN - 1 - i] *= env
        }
        return w
    }

    /** matched filter |xcorr| via overlap-save; returns magnitude array sized len(x)-CHIRP_LEN+1 */
    fun chirpMatchedFilter(x: DoubleArray): DoubleArray {
        val n = 16384
        val hop = n - CHIRP_LEN
        val f = DFFT(n)
        val cRe = DoubleArray(n); val cIm = DoubleArray(n)
        val ch = chirp()
        // correlation = convolution with reversed chirp
        for (i in 0 until CHIRP_LEN) cRe[i] = ch[CHIRP_LEN - 1 - i]
        f.transform(cRe, cIm, true)
        val outLen = x.size - CHIRP_LEN + 1
        if (outLen <= 0) return DoubleArray(0)
        val out = DoubleArray(outLen)
        var pos = 0
        val bRe = DoubleArray(n); val bIm = DoubleArray(n)
        while (pos < outLen) {
            java.util.Arrays.fill(bRe, 0.0); java.util.Arrays.fill(bIm, 0.0)
            val m = min(n, x.size - pos)
            for (i in 0 until m) bRe[i] = x[pos + i]
            f.transform(bRe, bIm, true)
            for (i in 0 until n) {
                val rr = bRe[i] * cRe[i] - bIm[i] * cIm[i]
                val ii = bRe[i] * cIm[i] + bIm[i] * cRe[i]
                bRe[i] = rr; bIm[i] = ii
            }
            f.transform(bRe, bIm, false)
            // conv result index k corresponds to xcorr at k - (CHIRP_LEN-1)
            val lim = min(hop, outLen - pos)
            for (i in 0 until lim) {
                val k = i + CHIRP_LEN - 1
                out[pos + i] = sqrt(bRe[k] * bRe[k] + bIm[k] * bIm[k]) / n
            }
            pos += hop
        }
        return out
    }

    fun findAllChirps(x: DoubleArray, frameSamples: Int, maxFrames: Int): List<Int> {
        val mf = chirpMatchedFilter(x)
        if (mf.isEmpty()) return emptyList()
        var mx = 0.0
        for (v in mf) if (v > mx) mx = v
        val thr = mx * 0.4
        val peaks = ArrayList<Int>()
        val m = mf.copyOf()
        for (rep in 0 until maxFrames + 4) {
            var k = 0
            var best = 0.0
            for (i in m.indices) if (m[i] > best) { best = m[i]; k = i }
            if (best < thr) break
            peaks.add(k)
            val lo = max(0, k - frameSamples / 2)
            val hi = min(m.size, k + frameSamples / 2)
            java.util.Arrays.fill(m, lo, hi, 0.0)
        }
        peaks.sort()
        return peaks
    }

    // ---------------- Viterbi (K=7, 171/133) ----------------
    private val NEXT = Array(64) { s -> IntArray(2) { b -> ((b shl 6) or s) shr 1 } }
    private val OUT0 = Array(64) { s -> IntArray(2) { b -> Integer.bitCount(((b shl 6) or s) and 0x79) and 1 } }  // 0o171
    private val OUT1 = Array(64) { s -> IntArray(2) { b -> Integer.bitCount(((b shl 6) or s) and 0x5B) and 1 } }  // 0o133

    fun viterbi(llr0: DoubleArray, llr1: DoubleArray, nInfo: Int): ByteArray {
        val n = llr0.size
        var metrics = DoubleArray(64) { -1e12 }
        metrics[0] = 0.0
        val back = Array(n) { ByteArray(64) }
        var newM = DoubleArray(64)
        for (i in 0 until n) {
            val l0 = llr0[i]; val l1 = llr1[i]
            java.util.Arrays.fill(newM, -1e18)
            val bk = back[i]
            for (s in 0 until 64) {
                val ms = metrics[s]
                if (ms < -1e11) continue
                for (b in 0 until 2) {
                    val bm = (if (OUT0[s][b] == 0) 0.5 * l0 else -0.5 * l0) +
                             (if (OUT1[s][b] == 0) 0.5 * l1 else -0.5 * l1)
                    val cand = ms + bm
                    val nx = NEXT[s][b]
                    if (cand > newM[nx]) {
                        newM[nx] = cand
                        bk[nx] = ((s shl 1) or b).toByte()
                    }
                }
            }
            val t = metrics; metrics = newM; newM = t
        }
        var s = 0
        val bits = ByteArray(n)
        for (i in n - 1 downTo 0) {
            val o = back[i][s].toInt() and 0x7F
            bits[i] = (o and 1).toByte()
            s = o shr 1
        }
        return bits.copyOf(nInfo)
    }

    // ---------------- 16-QAM max-log LLR (Gray, matches qam_llr) ----------------
    // axis levels /sqrt(10): -3,-1,1,3 ; gray order from _gray_levels(2):
    // gray codes of 0..3 = [0,1,3,2]; order = argsort = [0,1,3,2]
    // bit0 (MSB of axis): 1 for levels at positions where gray>>1==1
    private val LV = doubleArrayOf(-3.0, -1.0, 1.0, 3.0).map { it / sqrt(10.0) }.toDoubleArray()
    private val GOFPOS = intArrayOf(0, 1, 3, 2)  // gray index g of level position p

    private fun axisLlr(y: Double, n0: Double, out: DoubleArray, off: Int) {
        var d0b0 = 1e18; var d1b0 = 1e18; var d0b1 = 1e18; var d1b1 = 1e18
        for (p in 0 until 4) {
            val d = (y - LV[p]) * (y - LV[p])
            val g = GOFPOS[p]
            if ((g shr 1) and 1 == 1) { if (d < d1b0) d1b0 = d } else { if (d < d0b0) d0b0 = d }
            if (g and 1 == 1) { if (d < d1b1) d1b1 = d } else { if (d < d0b1) d0b1 = d }
        }
        out[off] = (d1b0 - d0b0) / n0
        out[off + 1] = (d1b1 - d0b1) / n0
    }

    // ---------------- main entry ----------------
    data class FrameResult(
        val start: Int,
        val ok: Boolean,
        val err: String?,
        val blocksOk: Int,
        val blocksTotal: Int,
        val verified: Int,
        val attributedFrame: Int?,
        val verifiedBlockIndices: List<Int>,
        val diagnosticVerified: Int,
        val diagnosticAttributedFrame: Int?,
        val evm: Double,
        val decodeMs: Long,
    )

    private data class RawFrameResult(
        val start: Int,
        val ok: Boolean,
        val err: String?,
        val blocksOk: Int,
        val blocksTotal: Int,
        val blocks: List<ByteArray?>,
        val evm: Double,
        val decodeMs: Long = 0,
    )

    fun decodeCapture(path: String, channels: Int, fLo: Double, fHi: Double,
                      nSym: Int, nPayloads: Int, payloadSeedBase: Long,
                      scheduleOriginSample: Int?, gapSamples: Int, slotToleranceSamples: Int,
                      log: (String) -> Unit): List<FrameResult> {
        val cfg = Cfg(fLo, fHi, nSym)
        val bytes = File(path).readBytes()
        val bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        val nFrames = bytes.size / 2 / channels
        val x = DoubleArray(nFrames)
        for (i in 0 until nFrames) {
            x[i] = bb.getShort((i * channels) * 2).toDouble() / 32768.0
        }
        log("bulk_decode: ${x.size} samples (${"%.1f".format(x.size.toDouble() / SRATE)}s) " +
            "band ${fLo.toInt()}-${fHi.toInt()} nSym=$nSym blocks=${cfg.nBlocks} " +
            "payload=${cfg.payloadBytes}B dataBins=${cfg.dataPos.size}")

        require(nPayloads > 0) { "nPayloads must be positive" }
        val expectedPayloads = (0 until nPayloads).map { payloadIndex ->
            DetRng(payloadSeedBase + payloadIndex).bytes(cfg.payloadBytes)
        }

        val starts = findAllChirps(x, cfg.frameSamples, nPayloads)
        log("bulk_decode: ${starts.size} chirp(s) found")
        val rawResults = starts.map { start ->
            val t0 = System.currentTimeMillis()
            demodFrame(cfg, x, start).copy(decodeMs = System.currentTimeMillis() - t0)
        }
        val candidates = rawResults.mapIndexedNotNull { candidateId, result ->
            if (result.ok) {
                val scheduledFrame = scheduleOriginSample?.let { origin ->
                    BulkAttribution.scheduledFrameIndex(
                        startSample = result.start,
                        scheduleOriginSample = origin,
                        frameSamples = cfg.frameSamples,
                        gapSamples = gapSamples,
                        frameCount = nPayloads,
                        toleranceSamples = slotToleranceSamples,
                    )
                }
                BulkAttribution.Candidate(candidateId, result.blocks, scheduledFrame)
            } else {
                null
            }
        }
        val strictByCandidate = BulkAttribution.attributeStrict(
            expectedPayloads,
            candidates,
            CRC_BLOCK,
        ).associateBy { it.candidateId }
        val diagnosticByCandidate = BulkAttribution.attributeByContentDiagnostic(
            expectedPayloads,
            candidates,
            CRC_BLOCK,
        ).associateBy { it.candidateId }
        val results = rawResults.mapIndexed { candidateId, raw ->
            val strict = strictByCandidate[candidateId]
            val diagnostic = diagnosticByCandidate[candidateId]
            FrameResult(
                start = raw.start,
                ok = raw.ok,
                err = raw.err,
                blocksOk = raw.blocksOk,
                blocksTotal = raw.blocksTotal,
                verified = strict?.verifiedBlocks ?: 0,
                attributedFrame = strict?.expectedFrameIndex,
                verifiedBlockIndices = strict?.verifiedBlockIndices ?: emptyList(),
                diagnosticVerified = diagnostic?.verifiedBlocks ?: 0,
                diagnosticAttributedFrame = diagnostic?.expectedFrameIndex,
                evm = raw.evm,
                decodeMs = raw.decodeMs,
            )
        }
        for (result in results) {
            val frameLabel = result.attributedFrame?.toString() ?: "none"
            val diagnosticLabel = result.diagnosticAttributedFrame?.toString() ?: "none"
            log(
                "bulk_decode frame@${"%.2f".format(result.start.toDouble() / SRATE)}s: " +
                    if (result.ok) {
                        "blocks=${result.blocksOk}/${result.blocksTotal} " +
                            "verified=${result.verified} scheduled_payload#$frameLabel " +
                            "diagnostic_verified=${result.diagnosticVerified} " +
                            "diagnostic_payload#$diagnosticLabel " +
                            "evm=${"%.3f".format(result.evm)} decode_ms=${result.decodeMs}"
                    } else {
                        "FAILED ${result.err} decode_ms=${result.decodeMs}"
                    },
            )
        }
        return results
    }

    private fun fftUsed(cfg: Cfg, x: DoubleArray, pos: Int): Pair<DoubleArray, DoubleArray>? {
        if (pos + CP + NFFT > x.size || pos < 0) return null
        val re = DoubleArray(NFFT)
        val im = DoubleArray(NFFT)
        System.arraycopy(x, pos + CP, re, 0, NFFT)
        fft2048.transform(re, im, true)
        val ur = DoubleArray(cfg.used.size)
        val ui = DoubleArray(cfg.used.size)
        for (i in cfg.used.indices) { ur[i] = re[cfg.used[i]]; ui[i] = im[cfg.used[i]] }
        return Pair(ur, ui)
    }

    private fun demodFrame(cfg: Cfg, x: DoubleArray, chirpStart: Int): RawFrameResult {
        var base = chirpStart + CHIRP_LEN + GUARD
        // fine sync via xcorr with sync symbol 0 reference
        val (s0re, s0im) = syncSymbolFreq(cfg, 0)
        val ref = ofdmModSymbol(cfg, s0re, s0im)
        val lo = max(0, base - 400)
        var bestOff = 0
        var bestV = -1.0
        for (off in 0..800) {
            val p = lo + off
            if (p + SYM > x.size) break
            var acc = 0.0
            var i = 0
            while (i < SYM) { acc += x[p + i] * ref[i]; i++ }
            if (abs(acc) > bestV) { bestV = abs(acc); bestOff = off }
        }
        base = lo + bestOff - 24   // early bias: pre-cursors stay inside CP

        // channel estimate from the two sync symbols
        val (s1re, s1im) = syncSymbolFreq(cfg, 1)
        val y0 = fftUsed(cfg, x, base) ?: return fail(chirpStart, "short capture sync0")
        val y1 = fftUsed(cfg, x, base + SYM) ?: return fail(chirpStart, "short capture sync1")
        val nb = cfg.used.size
        val h0r = DoubleArray(nb); val h0i = DoubleArray(nb)
        val h1r = DoubleArray(nb); val h1i = DoubleArray(nb)
        for (i in 0 until nb) {
            // H = Y / X with |X|=1: Y * conj(X)
            h0r[i] = y0.first[i] * s0re[i] + y0.second[i] * s0im[i]
            h0i[i] = y0.second[i] * s0re[i] - y0.first[i] * s0im[i]
            h1r[i] = y1.first[i] * s1re[i] + y1.second[i] * s1im[i]
            h1i[i] = y1.second[i] * s1re[i] - y1.first[i] * s1im[i]
        }
        val hr = DoubleArray(nb); val hi = DoubleArray(nb)
        val nv = DoubleArray(nb)
        for (i in 0 until nb) {
            hr[i] = (h0r[i] + h1r[i]) / 2
            hi[i] = (h0i[i] + h1i[i]) / 2
            val dr = h0r[i] - h1r[i]; val di = h0i[i] - h1i[i]
            nv[i] = (dr * dr + di * di) / 2
        }
        // smooth noise variance over 9 bins
        val nvs = DoubleArray(nb)
        for (i in 0 until nb) {
            var acc = 0.0; var c = 0
            for (j in max(0, i - 4)..min(nb - 1, i + 4)) { acc += nv[j]; c++ }
            nvs[i] = acc / c + 1e-12
        }
        val snr = DoubleArray(nb)
        for (i in 0 until nb) snr[i] = (hr[i] * hr[i] + hi[i] * hi[i]) / nvs[i]

        // demodulate data symbols
        val cap = cfg.bitsPerSym * cfg.nSym
        val llrStream = DoubleArray(cap)
        var pos = 0
        var evmAcc = 0.0
        val pn = cfg.pilotPos.size
        val pilBin0 = cfg.used[cfg.pilotPos[0]].toDouble()
        val pilStep = (cfg.used[cfg.pilotPos[1]] - cfg.used[cfg.pilotPos[0]]).toDouble()
        for (s in 0 until cfg.nSym) {
            val y = fftUsed(cfg, x, base + (2 + s) * SYM)
                ?: return fail(chirpStart, "short capture at sym $s")
            val zr = DoubleArray(nb); val zi = DoubleArray(nb)
            for (i in 0 until nb) {
                val den = hr[i] * hr[i] + hi[i] * hi[i] + 1e-15
                zr[i] = (y.first[i] * hr[i] + y.second[i] * hi[i]) / den
                zi[i] = (y.second[i] * hr[i] - y.first[i] * hi[i]) / den
            }
            // pilot iterative slope + CPE fit
            val er = DoubleArray(pn); val ei = DoubleArray(pn)
            for (k in 0 until pn) {
                val p = cfg.pilotPos[k]
                er[k] = zr[p] * cfg.pilotsRe[k] + zi[p] * cfg.pilotsIm[k]
                ei[k] = zi[p] * cfg.pilotsRe[k] - zr[p] * cfg.pilotsIm[k]
            }
            var slopeTot = 0.0
            for (iter in 0 until 3) {
                var dr = 0.0; var di = 0.0
                for (k in 1 until pn) {
                    dr += er[k] * er[k - 1] + ei[k] * ei[k - 1]
                    di += ei[k] * er[k - 1] - er[k] * ei[k - 1]
                }
                val slope = atan2(di, dr) / pilStep
                slopeTot += slope
                for (k in 0 until pn) {
                    val binOff = cfg.used[cfg.pilotPos[k]] - pilBin0
                    val c = cos(slope * binOff); val sn = sin(slope * binOff)
                    val nr = er[k] * c + ei[k] * sn
                    val ni = ei[k] * c - er[k] * sn
                    er[k] = nr; ei[k] = ni
                }
            }
            var sr = 0.0; var si = 0.0
            for (k in 0 until pn) { sr += er[k]; si += ei[k] }
            val ph0 = atan2(si, sr)
            // apply correction to all bins
            for (i in 0 until nb) {
                val ang = ph0 + slopeTot * (cfg.used[i] - pilBin0)
                val c = cos(ang); val sn = sin(ang)
                val nr = zr[i] * c + zi[i] * sn
                val ni = zi[i] * c - zr[i] * sn
                zr[i] = nr; zi[i] = ni
            }
            // pilot EVM after correction
            var evm2 = 0.0
            for (k in 0 until pn) {
                val p = cfg.pilotPos[k]
                val pr = zr[p] * cfg.pilotsRe[k] + zi[p] * cfg.pilotsIm[k]
                val pi = zi[p] * cfg.pilotsRe[k] - zr[p] * cfg.pilotsIm[k]
                evm2 += (pr - 1) * (pr - 1) + pi * pi
            }
            evm2 /= pn
            evmAcc += sqrt(evm2)
            // demap data bins (16-QAM)
            for (d in cfg.dataPos) {
                val n0 = 1.0 / max(snr[d], 0.1) + evm2
                axisLlr(zr[d], n0, llrStream, pos)
                axisLlr(zi[d], n0, llrStream, pos + 2)
                pos += 4
            }
        }

        // deinterleave
        val perm = DetRng(0x1EAF).permutation(cap)
        val llr = DoubleArray(cap)
        for (i in 0 until cap) llr[i] = llrStream[perm[i]]
        // depuncture to full rate-1/2 stream
        val nCodedFull = (cfg.infoBits + 6) * 2
        val full = DoubleArray(nCodedFull)
        var li = 0
        for (i in 0 until nCodedFull) {
            if (PUNCTURE[i % PUNCTURE.size] == 1) {
                full[i] = llr[li]; li++
            }
        }
        val llr0 = DoubleArray(nCodedFull / 2)
        val llr1 = DoubleArray(nCodedFull / 2)
        for (i in llr0.indices) { llr0[i] = full[2 * i]; llr1[i] = full[2 * i + 1] }
        val bits = viterbi(llr0, llr1, cfg.infoBits)

        // pack bits -> bytes; CRC per block; verify content
        val streamBytes = ByteArray(cfg.nBlocks * (CRC_BLOCK + 4))
        for (i in streamBytes.indices) {
            var v = 0
            for (b in 0 until 8) v = (v shl 1) or bits[i * 8 + b].toInt()
            streamBytes[i] = v.toByte()
        }
        var ok = 0
        val blocks = MutableList<ByteArray?>(cfg.nBlocks) { null }
        val crc = CRC32()
        for (j in 0 until cfg.nBlocks) {
            val off = j * (CRC_BLOCK + 4)
            crc.reset()
            crc.update(streamBytes, off, CRC_BLOCK)
            val v = crc.value
            val want = (((streamBytes[off + CRC_BLOCK].toLong() and 0xFF) shl 24) or
                        ((streamBytes[off + CRC_BLOCK + 1].toLong() and 0xFF) shl 16) or
                        ((streamBytes[off + CRC_BLOCK + 2].toLong() and 0xFF) shl 8) or
                        (streamBytes[off + CRC_BLOCK + 3].toLong() and 0xFF))
            if (v == want) {
                ok++
                blocks[j] = streamBytes.copyOfRange(off, off + CRC_BLOCK)
            }
        }
        return RawFrameResult(
            start = chirpStart,
            ok = true,
            err = null,
            blocksOk = ok,
            blocksTotal = cfg.nBlocks,
            blocks = blocks,
            evm = evmAcc / cfg.nSym,
        )
    }

    private fun fail(start: Int, err: String) =
        RawFrameResult(start, false, err, 0, 0, emptyList(), 0.0)
}
