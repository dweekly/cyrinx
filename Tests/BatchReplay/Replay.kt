import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

data class Decoded(val identity: ByteArray, val mask: ByteArray, val payload: ByteArray,
                   val receiver: Int, val reason: Int)

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
                check(result.mask.all { it == 1.toByte() } && result.payload.contentEquals(source))
            }
            check(runCatching { encode(-1, byteArrayOf()) }.exceptionOrNull() is IllegalArgumentException)
            check(runCatching { decode(1, floatArrayOf(0f), 2, false) }.exceptionOrNull() is IllegalArgumentException)
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
        System.out.write(result.identity)
        System.out.write(ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(result.receiver).putInt(result.reason).array())
        System.out.write(result.mask)
        System.out.write(result.payload)
        System.out.flush()
    }
}
