package com.dweekly.cyrinxhil

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Build
import android.os.Process
import android.util.Log
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt
import kotlin.math.sin

private const val TAG = "CyrinxHILAndroid"

data class AudioDiagnostics(
    val state: String,
    val txFrameCount: Long,
    val txByteCount: Long,
    val rxCallbackCount: Long,
    val outputCallbackCount: Long,
    val pendingOutputSampleCount: Long,
    val configuredSampleRateHz: Int,
    val observedInputSampleRateHz: Int,
    val observedOutputSampleRateHz: Int,
)

class AndroidAudioBackend(
    private val context: Context,
    private val config: SessionConfig,
    private val frameIngress: (ByteArray, ChannelReport) -> Int,
    private val logSink: (String) -> Unit = {},
) : FrameTxSink {
    private val running = AtomicBoolean(false)
    private val txFrameCount = AtomicLong(0)
    private val txByteCount = AtomicLong(0)
    private val rxCallbackCount = AtomicLong(0)
    private val rxDecodedFrameCount = AtomicLong(0)
    private val outputCallbackCount = AtomicLong(0)
    private val pendingOutputSampleCount = AtomicLong(0)

    private val txQueue = LinkedBlockingQueue<FloatArray>()
    private val rxDecodeQueue = LinkedBlockingQueue<FloatArray>(96)
    private val phy = AcousticPhyLink(config)

    @Volatile
    private var state: String = "idle"

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
    private var txThread: Thread? = null
    @Volatile
    private var rxDecodeThread: Thread? = null

    fun start(): Int {
        if (running.get()) {
            return CyrinxStatus.OK
        }

        val sampleRate = config.sampleRateHz
        val inMin = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val outMin = AudioTrack.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )

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
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
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

        val recordDevice = record

        audioRecord = recordDevice
        audioTrack = track
        observedInputSampleRateHz = sampleRate
        observedOutputSampleRateHz = sampleRate

        running.set(true)
        state = "running"

        recordDevice.startRecording()
        track.play()

        rxThread = Thread({ rxLoop(recordDevice) }, "cyrinx-android-rx").apply { start() }
        rxDecodeThread = Thread({ rxDecodeLoop() }, "cyrinx-android-rx-decode").apply { start() }
        txThread = Thread({ txLoop(track) }, "cyrinx-android-tx").apply { start() }

        logSink("android backend started cfgHz=$sampleRate inHz=$sampleRate outHz=$sampleRate source=$chosenSource")
        Log.i(TAG, "audio backend started")
        return CyrinxStatus.OK
    }

    fun stop() {
        if (!running.getAndSet(false)) {
            return
        }

        rxThread?.interrupt()
        rxDecodeThread?.interrupt()
        txThread?.interrupt()

        val record = audioRecord
        val track = audioTrack

        try {
            record?.stop()
        } catch (_: Throwable) {
        }
        try {
            track?.stop()
        } catch (_: Throwable) {
        }

        rxThread?.join(1_000)
        rxDecodeThread?.join(1_000)
        txThread?.join(1_000)

        record?.release()
        track?.release()

        audioRecord = null
        audioTrack = null
        rxThread = null
        rxDecodeThread = null
        txThread = null

        txQueue.clear()
        rxDecodeQueue.clear()
        pendingOutputSampleCount.set(0)
        state = "stopped"
        logSink("android backend stopped")
        Log.i(TAG, "audio backend stopped")
    }

    override fun sendFrame(frame: ByteArray): Int {
        txFrameCount.incrementAndGet()
        txByteCount.addAndGet(frame.size.toLong())
        val waveform = try {
            phy.encode(frame)
        } catch (t: Throwable) {
            Log.e(TAG, "encode failed", t)
            return CyrinxStatus.ERR_INTERNAL
        }
        pendingOutputSampleCount.addAndGet(waveform.size.toLong())
        if (!txQueue.offer(waveform)) {
            return CyrinxStatus.ERR_BUSY
        }
        if (txFrameCount.get() <= 10) {
            var peak = 0f
            for (sample in waveform) {
                peak = max(peak, abs(sample))
            }
            logSink("tx frame bytes=${frame.size} samples=${waveform.size} peak=$peak")
        }
        return CyrinxStatus.OK
    }

    fun playLocalAudibleBeacon(role: Role): Int {
        if (!running.get()) {
            return CyrinxStatus.ERR_NOT_RUNNING
        }
        val sampleRate = if (observedOutputSampleRateHz > 0) observedOutputSampleRateHz else config.sampleRateHz
        val waveform = synthesizeAudibleBeacon(role, sampleRate.toDouble(), config.txGainCap)
        txFrameCount.incrementAndGet()
        txByteCount.addAndGet(waveform.size.toLong())
        pendingOutputSampleCount.addAndGet(waveform.size.toLong())
        txQueue.offer(waveform)
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
        val buffer = ShortArray(2048)
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
            if (!rxDecodeQueue.offer(samples)) {
                rxDecodeQueue.poll()
                rxDecodeQueue.offer(samples)
                logSink("rx decode queue overflow: dropped oldest chunk")
            }
            val callbackCount = rxCallbackCount.get()
            if (callbackCount % 40L == 0L) {
                val rms = sqrt(max(0.0, energy / max(1, read))).toFloat()
                logSink(
                    "rx callbacks=$callbackCount rms=$rms decodedTotal=${rxDecodedFrameCount.get()} " +
                        "decodeQueueDepth=${rxDecodeQueue.size}",
                )
            }
        }
    }

    private fun rxDecodeLoop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_DEFAULT)
        while (running.get()) {
            val first = rxDecodeQueue.poll(120, TimeUnit.MILLISECONDS) ?: continue
            val batch = ArrayList<FloatArray>(8)
            batch.add(first)
            rxDecodeQueue.drainTo(batch, 7)

            val totalSamples = batch.sumOf { it.size }
            val samples = FloatArray(totalSamples)
            var cursor = 0
            for (chunk in batch) {
                System.arraycopy(chunk, 0, samples, cursor, chunk.size)
                cursor += chunk.size
            }

            val decoded = try {
                phy.ingest(samples)
            } catch (t: Throwable) {
                Log.e(TAG, "ingest failed", t)
                continue
            }
            if (decoded.isNotEmpty()) {
                rxDecodedFrameCount.addAndGet(decoded.size.toLong())
                logSink("decoded acoustic frames=${decoded.size} total=${rxDecodedFrameCount.get()}")
            }
            for (frame in decoded) {
                frameIngress(frame.frame, frame.report)
            }
        }
    }

    private fun txLoop(track: AudioTrack) {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        while (running.get()) {
            val waveform = txQueue.poll(100, TimeUnit.MILLISECONDS) ?: continue
            val pcm = ShortArray(waveform.size)
            for (i in waveform.indices) {
                val clamped = waveform[i].coerceIn(-1f, 1f)
                pcm[i] = (clamped * 32767.0f).toInt().toShort()
            }

            var offset = 0
            while (offset < pcm.size && running.get()) {
                val wrote = track.write(pcm, offset, pcm.size - offset, AudioTrack.WRITE_BLOCKING)
                if (wrote <= 0) {
                    break
                }
                offset += wrote
                outputCallbackCount.incrementAndGet()
                pendingOutputSampleCount.addAndGet(-wrote.toLong())
            }
        }
    }

    private fun synthesizeAudibleBeacon(role: Role, sampleRate: Double, txGainCap: Float): FloatArray {
        val fs = sampleRate.coerceIn(8_000.0, 192_000.0)
        val amplitude = min(max(txGainCap, 0f), 0.30f)
        if (amplitude <= 0f) {
            return FloatArray(0)
        }

        val pattern = listOf(
            440.0 to 0.28,
            0.0 to 0.08,
            880.0 to 0.28,
            0.0 to 0.08,
            440.0 to 0.28,
        )

        val out = ArrayList<Float>()
        for ((freq, durationSec) in pattern) {
            val count = max(1, (durationSec * fs).toInt())
            if (freq <= 0.0) {
                repeat(count) { out.add(0f) }
                continue
            }
            val ramp = min(max(16, (fs * 0.02).toInt()), count / 2)
            var phase = 0f
            val phaseStep = (2.0 * Math.PI * freq / fs).toFloat()
            repeat(count) { idx ->
                val envelope = when {
                    idx < ramp -> {
                        val x = idx / max(1f, ramp.toFloat())
                        0.5f - (0.5f * kotlin.math.cos(Math.PI.toFloat() * x))
                    }

                    idx >= (count - ramp) -> {
                        val remaining = (count - idx - 1).toFloat()
                        val x = remaining / max(1f, ramp.toFloat())
                        0.5f - (0.5f * kotlin.math.cos(Math.PI.toFloat() * x))
                    }

                    else -> 1f
                }
                out.add((amplitude * max(0f, envelope) * sin(phase)))
                phase += phaseStep
            }
        }

        val roleScale = if (role == Role.MASTER) 1.0f else 0.95f
        val wave = FloatArray(out.size)
        for (i in out.indices) {
            wave[i] = out[i] * roleScale
        }
        val peak = wave.maxOfOrNull { abs(it) } ?: 0f
        logSink("beacon generated samples=${wave.size} peak=$peak")
        return wave
    }
}
