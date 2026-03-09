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
import kotlin.math.max
import kotlin.math.sqrt

class BasicToneAndroidBackend(
    private val context: Context,
    private val config: SessionConfig,
    private val frameIngress: (ByteArray) -> Unit,
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
    private val codec = BasicToneCodec(config)

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
        val outMin = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
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

        audioRecord = record
        audioTrack = track
        observedInputSampleRateHz = sampleRate
        observedOutputSampleRateHz = sampleRate
        running.set(true)
        state = "running"

        record.startRecording()
        track.play()
        rxThread = Thread({ rxLoop(record) }, "basic-tone-rx").apply { start() }
        rxDecodeThread = Thread({ rxDecodeLoop() }, "basic-tone-rx-decode").apply { start() }
        txThread = Thread({ txLoop(track) }, "basic-tone-tx").apply { start() }

        logSink("basic tone backend started cfgHz=$sampleRate inHz=$sampleRate outHz=$sampleRate source=$chosenSource")
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
        rxDecodeThread?.join(1_000)
        txThread?.join(1_000)

        audioRecord?.release()
        audioTrack?.release()
        audioRecord = null
        audioTrack = null
        rxThread = null
        rxDecodeThread = null
        txThread = null
        txQueue.clear()
        rxDecodeQueue.clear()
        pendingOutputSampleCount.set(0)
        state = "stopped"
        logSink("basic tone backend stopped")
    }

    fun sendFrame(frame: ByteArray): Int {
        val waveform = codec.encode(frame)
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
            if (!rxDecodeQueue.offer(samples)) {
                rxDecodeQueue.poll()
                rxDecodeQueue.offer(samples)
                logSink("basic tone rx queue overflow: dropped oldest chunk")
            }
            if (rxCallbackCount.get() % 40L == 0L) {
                val rms = sqrt(max(0.0, energy / max(1, read))).toFloat()
                logSink("basic tone rx callbacks=${rxCallbackCount.get()} rms=$rms queue=${rxDecodeQueue.size}")
            }
        }
    }

    private fun rxDecodeLoop() {
        try {
            while (running.get()) {
                val samples = rxDecodeQueue.poll(120, TimeUnit.MILLISECONDS) ?: continue
                val decoded = codec.ingest(samples)
                for (frame in decoded) {
                    frameIngress(frame)
                }
            }
        } catch (_: InterruptedException) {
        }
    }

    private fun txLoop(track: AudioTrack) {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        try {
            while (running.get()) {
                val waveform = txQueue.poll(120, TimeUnit.MILLISECONDS) ?: continue
                outputCallbackCount.incrementAndGet()
                val pcm = ShortArray(waveform.size)
                for (i in waveform.indices) {
                    val clamped = waveform[i].coerceIn(-1f, 1f)
                    pcm[i] = (clamped * 32767.0f).toInt().toShort()
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
}
