import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** One batch decode as the C core reported it. `counts` holds the selected receiver, selection
 *  reason, total blocks, valid blocks, clipping evidence and non-finite evidence; `sizes` the
 *  payload bytes produced and logical samples decoded; `metrics` the EVM and the two pilot
 *  residuals. A rejected capture has a non-zero status and empty mask and payload. */
class Decoded(val status: Int, val identity: ByteArray, val mask: ByteArray, val payload: ByteArray,
              val counts: IntArray, val sizes: LongArray, val metrics: DoubleArray)

object Replay {
    external fun profileIDs(): IntArray
    external fun encode(profile: Int, payload: ByteArray): FloatArray
    external fun decode(profile: Int, samples: FloatArray, channels: Int, fixture: Boolean): Decoded

    @JvmStatic fun main(args: Array<String>) {
        System.load(args[0])
        if (args[1] == "registry") {
            val ids = profileIDs()
            check(ids.contentEquals(intArrayOf(1, 2, 3, 4, 5)))
            for (id in ids) {
                val capacity = decode(id, encode(id, byteArrayOf()), 1, false).payload.size
                val source = java.util.Random(id.toLong()).let { rng -> ByteArray(capacity).also { rng.nextBytes(it) } }
                val result = decode(id, encode(id, source), 1, false)
                check(result.status == 0 && result.mask.all { it == 1.toByte() } && result.payload.contentEquals(source))
                check(result.counts[2] == result.mask.size && result.counts[3] == result.mask.size)
                check(result.sizes[0] == capacity.toLong())
            }
            check(runCatching { encode(-1, byteArrayOf()) }.exceptionOrNull() is IllegalArgumentException)
            check(runCatching { decode(1, floatArrayOf(0f), 2, false) }.exceptionOrNull() is IllegalArgumentException)
            val rejected = decode(1, floatArrayOf(0f), 1, false)
            check(rejected.status == -1 && rejected.mask.isEmpty() && rejected.payload.isEmpty())
            print("registry OK")
            return
        }
        val data = File(args[2]).readBytes()
        require(data.isNotEmpty() && data.size % 4 == 0)
        val buffer = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
        val samples = FloatArray(buffer.remaining())
        buffer.get(samples)
        val channels = args[3].toInt()
        require(channels in 1..2 && samples.size % channels == 0)
        val result = decode(args[1].toInt(), samples, channels, true)
        // The record layout is Tests/BatchReplay/Packet.h.
        val header = ByteBuffer.allocate(100).order(ByteOrder.LITTLE_ENDIAN)
        header.putInt(result.status).put(result.identity)
        result.counts.forEach { header.putInt(it) }
        result.sizes.forEach { header.putLong(it) }
        result.metrics.forEach { header.putDouble(it) }
        System.out.write(header.array())
        System.out.write(result.mask)
        System.out.write(result.payload)
        System.out.flush()
    }
}
