import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

data class Decoded(val identity: ByteArray, val mask: ByteArray, val payload: ByteArray,
                   val receiver: Int, val reason: Int)

object Replay {
    external fun decode(profile: Int, samples: FloatArray, channels: Int): Decoded

    @JvmStatic fun main(args: Array<String>) {
        System.load(args[0])
        val data = File(args[2]).readBytes()
        require(data.isNotEmpty() && data.size % 4 == 0)
        val buffer = ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer()
        val samples = FloatArray(buffer.remaining())
        buffer.get(samples)
        val channels = args[3].toInt()
        require(channels in 1..2 && samples.size % channels == 0)
        val result = decode(args[1].toInt(), samples, channels)
        System.out.write(result.identity)
        System.out.write(ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
            .putInt(result.receiver).putInt(result.reason).array())
        System.out.write(result.mask)
        System.out.write(result.payload)
        System.out.flush()
    }
}
