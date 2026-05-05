package com.dweekly.cyrinxhil

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Build
import android.os.Process
import android.util.Log
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.max
import kotlin.math.sqrt

class BasicToneAndroidBackend(
    private val context: Context,
    private val config: SessionConfig,
    private val frameIngress: (ByteArray) -> Unit,
    private val capturePath: String? = null,
    private val logSink: (String) -> Unit = {},
) {
    private val running = AtomicBoolean(false)
    private val txFrameCount = AtomicLong(0)
    private val txByteCount = AtomicLong(0)
    private val rxCallbackCount = AtomicLong(0)
    private val outputCallbackCount = AtomicLong(0)
    private val pendingOutputSampleCount = AtomicLong(0)
    private val txQueue = LinkedBlockingQueue<FloatArray>()
    private val rxDecodeQueue = LinkedBlockingQueue<FloatArray>(64)
    private val bitCodec = BasicToneCodec(config)
    private val reverseBurstCodec = ReverseBurstCodec(config)
    private val ookCodec = OOKToneCodec(config)
    private val morseCodec = MorseToneCodec(config)
    private val dtmfCodec = DTMFCodec(config)
    private val nibbleCodec = NibbleToneCodec(config)
    private val rawCodec = resolveRawCodec(config)
    private val activityThreshold = when (rawCodec) {
        RawCodec.DTMF, RawCodec.NIBBLE, RawCodec.OOK, RawCodec.MORSE -> 0.0020f
        RawCodec.REVERSE_BURST -> 0.0018f
        RawCodec.BASIC, RawCodec.AUTO -> 0.0015f
    }
    private val preRollSamples = max(4_096, minOf(32_768, config.dcssSymbolSamples * 12))
    private val postRollChunks = 3
    private val captureLock = Object()
    private val capturedInputSamples = ArrayList<Float>()
    private val maxCaptureSamples = config.sampleRateHz * 60
    private var rxPreRoll = FloatArray(0)
    private var rxBurstOpen = false
    private var rxHangoverChunks = 0

    @Volatile
    private var state = "idle"
    @Volatile
    private var observedInputSampleRateHz = 0
    @Volatile
    private var observedOutputSampleRateHz = 0
    @Volatile
    private var audioRecord: AudioRecord? = null
    @Volatile
    private var audioTrack: AudioTrack? = null
    @Volatile
    private var rxThread: Thread? = null
    @Volatile
    private var rxDecodeThread: Thread? = null
    @Volatile
    private var txThread: Thread? = null

    fun start(): Int {
        if (running.get()) {
            return CyrinxStatus.OK
        }

        val sampleRate = config.sampleRateHz
        val inMin = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val outMin = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT)
        if (inMin <= 0 || outMin <= 0) {
            state = "failed"
            return CyrinxStatus.ERR_INTERNAL
        }

        val recordBufferSize = max(inMin * 4, sampleRate / 2)
        val trackBufferSize = max(outMin * 4, sampleRate / 2)
        val sourceCandidates = buildList {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                add(MediaRecorder.AudioSource.UNPROCESSED)
            }
            add(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            add(MediaRecorder.AudioSource.MIC)
        }

        var chosenSource: Int? = null
        var record: AudioRecord? = null
        for (candidate in sourceCandidates) {
            val attempt = AudioRecord(
                candidate,
                sampleRate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                recordBufferSize,
            )
            if (attempt.state == AudioRecord.STATE_INITIALIZED) {
                chosenSource = candidate
                record = attempt
                break
            }
            attempt.release()
        }
        if (record == null) {
            state = "failed"
            return CyrinxStatus.ERR_INTERNAL
        }

        val track = AudioTrack(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build(),
            AudioFormat.Builder()
                .setSampleRate(sampleRate)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                .build(),
            trackBufferSize,
            AudioTrack.MODE_STREAM,
            AudioManager.AUDIO_SESSION_ID_GENERATE,
        )
        if (track.state != AudioTrack.STATE_INITIALIZED) {
            record.release()
            track.release()
            state = "failed"
            return CyrinxStatus.ERR_INTERNAL
        }

        audioRecord = record
        audioTrack = track
        observedInputSampleRateHz = record.sampleRate
        observedOutputSampleRateHz = track.sampleRate
        running.set(true)
        state = "running"

        record.startRecording()
        track.play()
        rxThread = Thread({ rxLoop(record) }, "basic-tone-rx").apply { start() }
        rxDecodeThread = Thread({ rxDecodeLoop() }, "basic-tone-rx-decode").apply { start() }
        txThread = Thread({ txLoop(track) }, "basic-tone-tx").apply { start() }

        val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val outputPropHz = audioManager.getProperty(AudioManager.PROPERTY_OUTPUT_SAMPLE_RATE) ?: "unknown"
        val framesPerBuffer = audioManager.getProperty(AudioManager.PROPERTY_OUTPUT_FRAMES_PER_BUFFER) ?: "unknown"
        val inputDevice = describeDevice(record.routedDevice)
        val outputDevice = describeDevice(track.routedDevice)
        logSink(
            "basic tone backend started " +
                "cfgHz=$sampleRate inHz=${record.sampleRate} outHz=${track.sampleRate} " +
                "recordState=${record.recordingState} playState=${track.playState} " +
                "source=$chosenSource rawCodec=$rawCodec outPropHz=$outputPropHz framesPerBuffer=$framesPerBuffer " +
                "inputDevice=$inputDevice outputDevice=$outputDevice"
        )
        return CyrinxStatus.OK
    }

    fun stop() {
        if (!running.getAndSet(false)) {
            return
        }
        rxThread?.interrupt()
        rxDecodeThread?.interrupt()
        txThread?.interrupt()

        try {
            audioRecord?.stop()
        } catch (_: Throwable) {
        }
        try {
            audioTrack?.stop()
        } catch (_: Throwable) {
        }

        rxThread?.join(1_000)
        txThread?.join(1_000)
        rxDecodeThread?.join(3_000)
        drainDecodeQueue()

        audioRecord?.release()
        audioTrack?.release()
        audioRecord = null
        audioTrack = null
        rxThread = null
        rxDecodeThread = null
        txThread = null
        writeCapturedInputIfNeeded()
        txQueue.clear()
        rxDecodeQueue.clear()
        pendingOutputSampleCount.set(0)
        state = "stopped"
        logSink("basic tone backend stopped")
    }

    fun sendFrame(frame: ByteArray): Int {
        val waveform = when (rawCodec) {
            RawCodec.OOK -> ookCodec.encode(frame)
            RawCodec.MORSE -> morseCodec.encode(frame)
            RawCodec.REVERSE_BURST -> reverseBurstCodec.encode(frame)
            RawCodec.DTMF -> dtmfCodec.encode(frame)
            RawCodec.NIBBLE -> nibbleCodec.encode(frame)
            RawCodec.BASIC, RawCodec.AUTO -> bitCodec.encode(frame)
        }
        txFrameCount.incrementAndGet()
        txByteCount.addAndGet(frame.size.toLong())
        pendingOutputSampleCount.addAndGet(waveform.size.toLong())
        if (!txQueue.offer(waveform)) {
            return CyrinxStatus.ERR_BUSY
        }
        return CyrinxStatus.OK
    }

    fun diagnostics(): AudioDiagnostics {
        return AudioDiagnostics(
            state = state,
            txFrameCount = txFrameCount.get(),
            txByteCount = txByteCount.get(),
            rxCallbackCount = rxCallbackCount.get(),
            outputCallbackCount = outputCallbackCount.get(),
            pendingOutputSampleCount = pendingOutputSampleCount.get(),
            configuredSampleRateHz = config.sampleRateHz,
            observedInputSampleRateHz = observedInputSampleRateHz,
            observedOutputSampleRateHz = observedOutputSampleRateHz,
        )
    }

    private fun rxLoop(record: AudioRecord) {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        val batchSamples = max(8_192, minOf(16_384, config.dcssSymbolSamples * 8))
        val buffer = ShortArray(batchSamples)
        while (running.get()) {
            val read = record.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING)
            if (read <= 0) {
                continue
            }
            rxCallbackCount.incrementAndGet()
            val samples = FloatArray(read)
            var energy = 0.0
            for (i in 0 until read) {
                samples[i] = buffer[i] / 32768.0f
                energy += (samples[i] * samples[i]).toDouble()
            }
            captureInputSamples(samples)
            val rms = sqrt(max(0.0, energy / max(1, read))).toFloat()
            val gatedSamples = prepareDecodeChunk(samples, rms) ?: continue
            if (!rxDecodeQueue.offer(gatedSamples)) {
                rxDecodeQueue.poll()
                rxDecodeQueue.offer(gatedSamples)
                logSink("basic tone rx queue overflow: dropped oldest chunk")
            }
            if (rxCallbackCount.get() % 40L == 0L) {
                logSink("basic tone rx callbacks=${rxCallbackCount.get()} rms=$rms queue=${rxDecodeQueue.size}")
            }
        }
    }

    private fun rxDecodeLoop() {
        try {
            while (running.get()) {
                val samples = rxDecodeQueue.poll(120, TimeUnit.MILLISECONDS) ?: continue
                decodeSamples(samples)
            }
        } catch (_: InterruptedException) {
        }
    }

    private fun drainDecodeQueue() {
        while (true) {
            val samples = rxDecodeQueue.poll() ?: break
            decodeSamples(samples)
        }
    }

    private fun decodeSamples(samples: FloatArray) {
        val decoded = when (rawCodec) {
            RawCodec.OOK -> ookCodec.ingest(samples)
            RawCodec.MORSE -> morseCodec.ingest(samples)
            RawCodec.REVERSE_BURST -> reverseBurstCodec.ingest(samples)
            RawCodec.DTMF -> dtmfCodec.ingest(samples)
            RawCodec.NIBBLE -> nibbleCodec.ingest(samples)
            RawCodec.BASIC, RawCodec.AUTO -> bitCodec.ingest(samples)
        }
        for (frame in decoded) {
            frameIngress(frame)
        }
    }

    private fun txLoop(track: AudioTrack) {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        try {
            while (running.get()) {
                val waveform = txQueue.poll(120, TimeUnit.MILLISECONDS) ?: continue
                outputCallbackCount.incrementAndGet()
                val pcm = ShortArray(waveform.size * 2)
                for (i in waveform.indices) {
                    val clamped = waveform[i].coerceIn(-1f, 1f)
                    val sample = (clamped * 32767.0f).toInt().toShort()
                    val base = i * 2
                    pcm[base] = sample
                    pcm[base + 1] = sample
                }
                var offset = 0
                while (offset < pcm.size && running.get()) {
                    val written = track.write(pcm, offset, pcm.size - offset, AudioTrack.WRITE_BLOCKING)
                    if (written <= 0) {
                        Log.w("CyrinxBasicTone", "basic tone write failed written=$written")
                        break
                    }
                    offset += written
                }
                pendingOutputSampleCount.addAndGet(-waveform.size.toLong())
            }
        } catch (_: InterruptedException) {
        }
    }

    private fun describeDevice(device: AudioDeviceInfo?): String {
        if (device == null) {
            return "none"
        }
        val sampleRates = device.sampleRates?.takeIf { it.isNotEmpty() }?.joinToString(",") ?: "unspecified"
        return "type=${device.type} id=${device.id} product=${device.productName} rates=$sampleRates"
    }

    private fun prepareDecodeChunk(samples: FloatArray, rms: Float): FloatArray? {
        if (rms >= activityThreshold) {
            val combined = if (!rxBurstOpen && rxPreRoll.isNotEmpty()) {
                concat(rxPreRoll, samples)
            } else {
                samples
            }
            rxBurstOpen = true
            rxHangoverChunks = postRollChunks
            rxPreRoll = FloatArray(0)
            return combined
        }

        if (rxBurstOpen && rxHangoverChunks > 0) {
            rxHangoverChunks -= 1
            if (rxHangoverChunks == 0) {
                rxBurstOpen = false
            }
            return samples
        }

        rxBurstOpen = false
        rxHangoverChunks = 0
        rxPreRoll = if (samples.size <= preRollSamples) {
            samples.copyOf()
        } else {
            samples.copyOfRange(samples.size - preRollSamples, samples.size)
        }
        return null
    }

    private fun concat(prefix: FloatArray, suffix: FloatArray): FloatArray {
        val out = FloatArray(prefix.size + suffix.size)
        System.arraycopy(prefix, 0, out, 0, prefix.size)
        System.arraycopy(suffix, 0, out, prefix.size, suffix.size)
        return out
    }

    private fun captureInputSamples(samples: FloatArray) {
        if (capturePath == null || samples.isEmpty()) {
            return
        }
        synchronized(captureLock) {
            val overflow = (capturedInputSamples.size + samples.size) - maxCaptureSamples
            if (overflow > 0) {
                capturedInputSamples.subList(0, minOf(overflow, capturedInputSamples.size)).clear()
            }
            for (sample in samples) {
                capturedInputSamples.add(sample)
            }
        }
    }

    private fun writeCapturedInputIfNeeded() {
        val path = capturePath ?: return
        val snapshot = synchronized(captureLock) {
            capturedInputSamples.toFloatArray().also {
                capturedInputSamples.clear()
            }
        }
        if (snapshot.isEmpty()) {
            logSink("basic tone capture skipped: no samples path=$path")
            return
        }
        try {
            val out = ByteBuffer.allocate(snapshot.size * java.lang.Float.BYTES)
                .order(ByteOrder.LITTLE_ENDIAN)
            for (sample in snapshot) {
                out.putFloat(sample)
            }
            File(path).writeBytes(out.array())
            logSink("basic tone capture wrote path=$path samples=${snapshot.size}")
        } catch (t: Throwable) {
            logSink("basic tone capture failed path=$path error=${t.javaClass.simpleName}:${t.message}")
        }
    }

    private fun resolveRawCodec(config: SessionConfig): RawCodec {
        if (config.rawCodec != RawCodec.AUTO) {
            return config.rawCodec
        }
        if (config.bandStartHz == config.bandEndHz) {
            return RawCodec.OOK
        }
        if (config.role == Role.MASTER) {
            return RawCodec.REVERSE_BURST
        }
        return RawCodec.BASIC
    }
}
