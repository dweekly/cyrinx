package com.dweekly.cyrinxhil

import android.Manifest
import android.content.Context
import android.media.AudioManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.MicrophoneInfo
import android.os.Vibrator
import android.os.VibrationEffect
import android.os.Build
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

private const val TAG = "CyrinxHILAndroid"

class MainActivity : ComponentActivity() {
    private lateinit var roleSpinner: Spinner
    private lateinit var statusText: TextView
    private lateinit var diagnosticsText: TextView
    private lateinit var logText: TextView

    private val ioExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val logLines = ArrayList<String>()

    @Volatile
    private var session: CyrinxTransportSession? = null

    @Volatile
    private var backend: AndroidAudioBackend? = null
    @Volatile
    private var rawBackend: BasicToneAndroidBackend? = null
    @Volatile
    private var overrideSampleRateHz: Int? = null
    @Volatile
    private var overrideBandStartHz: Int? = null
    @Volatile
    private var overrideBandEndHz: Int? = null
    @Volatile
    private var overrideTxGainCap: Float? = null
    @Volatile
    private var overrideDcssSymbolSamples: Int? = null
    @Volatile
    private var overrideSyncThreshold: Float? = null
    @Volatile
    private var overrideRawCodec: RawCodec? = null
    @Volatile
    private var overrideRawCapturePath: String? = null
    @Volatile
    private var overrideChannels: Int? = null
    @Volatile
    private var overrideForceBodyMode: String? = null

    private val periodicRefresh = object : Runnable {
        override fun run() {
            refreshDiagnostics()
            mainHandler.postDelayed(this, 1_000)
        }
    }

    private val recordPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            appendLog("mic permission granted=$granted")
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // HIL automation runs with the device locked on a desk. Without these, the
        // keyguard/bouncer overlays the activity, it loses top-visibility, and the
        // audio policy silences mic capture (returns all zeros).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        roleSpinner = findViewById(R.id.roleSpinner)
        statusText = findViewById(R.id.statusText)
        diagnosticsText = findViewById(R.id.diagnosticsText)
        logText = findViewById(R.id.logText)

        setupRoleSpinner()
        setupButtons()
        setupAccessibilityMetadata()

        ensureMicPermission()
        mainHandler.post(periodicRefresh)

        runMIMOSelfTests()

        handleAutomationIntent(intent)
    }

    private fun runMIMOSelfTests() {
        try {
            // 1. Verify SVD solver on high condition number (highly correlated MIMO channel, should choose Diversity)
            val configStereo = SessionConfig(
                role = Role.SLAVE,
                sampleRateHz = 48000,
                bandStartHz = 18500,
                bandEndHz = 21000,
                txGainCap = 0.5f,
                preambleSyncThreshold = 0.52f,
                channels = 2
            )
            val linkStereo = AcousticPhyLink(configStereo)
            val pL = linkStereo.encode(byteArrayOf(1, 2, 3))
            val preambleLen = 1016
            val mockLeft = FloatArray(preambleLen)
            val mockRight = FloatArray(preambleLen)
            
            val x1 = FloatArray(preambleLen)
            val x2 = FloatArray(preambleLen)
            for (i in 0 until preambleLen) {
                x1[i] = pL[i * 2]
                x2[i] = pL[i * 2 + 1]
            }
            
            for (i in 0 until preambleLen) {
                mockLeft[i] = 1.0f * x1[i] + 0.95f * x2[i]
                mockRight[i] = 0.95f * x1[i] + 1.0f * x2[i]
            }
            
            val mockInterleaved = FloatArray(preambleLen * 2)
            for (i in 0 until preambleLen) {
                mockInterleaved[i * 2] = mockLeft[i]
                mockInterleaved[i * 2 + 1] = mockRight[i]
            }
            
            linkStereo.ingest(mockInterleaved)
            
            check(linkStereo.lastH11 > 0.65f) { "H11 check failed: ${linkStereo.lastH11}" }
            check(linkStereo.lastH22 > 0.65f) { "H22 check failed: ${linkStereo.lastH22}" }
            check(linkStereo.lastH12 > 0.60f) { "H12 check failed: ${linkStereo.lastH12}" }
            check(linkStereo.lastH21 > 0.60f) { "H21 check failed: ${linkStereo.lastH21}" }
            check(linkStereo.lastSpatialMode == 0) { "SpatialMode check failed (correlated): ${linkStereo.lastSpatialMode}" }
            
            // 2. Verify SVD solver on orthogonal/low condition number (should choose Multiplexing)
            val linkStereo2 = AcousticPhyLink(configStereo)
            for (i in 0 until preambleLen) {
                mockLeft[i] = 1.0f * x1[i] + 0.05f * x2[i]
                mockRight[i] = 0.05f * x1[i] + 1.0f * x2[i]
            }
            for (i in 0 until preambleLen) {
                mockInterleaved[i * 2] = mockLeft[i]
                mockInterleaved[i * 2 + 1] = mockRight[i]
            }
            linkStereo2.ingest(mockInterleaved)
            check(linkStereo2.lastSpatialMode == 1) { "SpatialMode check failed (orthogonal): ${linkStereo2.lastSpatialMode}" }
            
            // 3. Verify THD measurement math on a pure 3kHz sine tone vs distorted tone
            val fs = 48000f
            val f0 = 3000f
            val cleanTone = linkStereo.generateSineTone(f0, 0.1f, fs, 0.5f)
            val cleanTHD = linkStereo.calculateTHD(cleanTone, fs.toInt(), f0)
            check(cleanTHD < 0.5f) { "Clean THD check failed: $cleanTHD" }
            
            val distortedTone = cleanTone.clone()
            val h2Tone = linkStereo.generateSineTone(f0 * 2f, 0.1f, fs, 0.05f)
            val h3Tone = linkStereo.generateSineTone(f0 * 3f, 0.1f, fs, 0.025f)
            for (i in distortedTone.indices) {
                distortedTone[i] += h2Tone[i] + h3Tone[i]
            }
            val distTHD = linkStereo.calculateTHD(distortedTone, fs.toInt(), f0)
            check(distTHD in 9.0f..13.0f) { "Distorted THD check failed: $distTHD" }
            
            appendLog("MIMO 2x2 SVD & THD self-tests passed successfully!")
            Log.i(TAG, "MIMO 2x2 SVD & THD self-tests passed successfully!")
        } catch (e: Exception) {
            appendLog("MIMO self-tests failed: ${e.message}")
            Log.e(TAG, "MIMO self-tests failed", e)
        }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleAutomationIntent(intent)
    }

    override fun onDestroy() {
        super.onDestroy()
        mainHandler.removeCallbacks(periodicRefresh)
        stopSession()
        stopRawBackend()
        ioExecutor.shutdownNow()
    }

    private fun setupRoleSpinner() {
        val roles = listOf("Master", "Slave")
        roleSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, roles)
        roleSpinner.setSelection(0)
    }

    private fun setupButtons() {
        findViewById<Button>(R.id.startButton).setOnClickListener {
            startSession(selectedRole())
        }
        findViewById<Button>(R.id.stopButton).setOnClickListener {
            stopSession()
        }
        findViewById<Button>(R.id.sendProbeButton).setOnClickListener {
            sendProbe(bestEffort = true)
        }
        findViewById<Button>(R.id.sendReliableButton).setOnClickListener {
            sendProbe(bestEffort = false)
        }
        findViewById<Button>(R.id.receiveButton).setOnClickListener {
            receiveOnce()
        }
        findViewById<Button>(R.id.refreshButton).setOnClickListener {
            refreshDiagnostics()
        }
        findViewById<Button>(R.id.beaconButton).setOnClickListener {
            playBeacon()
        }
    }

    private fun setupAccessibilityMetadata() {
        roleSpinner.contentDescription = "role_spinner"
        findViewById<Button>(R.id.startButton).contentDescription = "start_button"
        findViewById<Button>(R.id.stopButton).contentDescription = "stop_button"
        findViewById<Button>(R.id.sendProbeButton).contentDescription = "send_probe_be_button"
        findViewById<Button>(R.id.sendReliableButton).contentDescription = "send_probe_reliable_button"
        findViewById<Button>(R.id.receiveButton).contentDescription = "receive_once_button"
        findViewById<Button>(R.id.refreshButton).contentDescription = "refresh_diagnostics_button"
        findViewById<Button>(R.id.beaconButton).contentDescription = "play_beacon_button"
    }

    private fun ensureMicPermission() {
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            recordPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun selectedRole(): Role {
        return if (roleSpinner.selectedItemPosition == 0) Role.MASTER else Role.SLAVE
    }

    private fun setRole(role: Role) {
        roleSpinner.setSelection(if (role == Role.MASTER) 0 else 1)
    }

    private fun startSession(role: Role) {
        val micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (!micGranted) {
            appendLog("cannot start: RECORD_AUDIO permission missing")
            ensureMicPermission()
            return
        }

        stopRawBackend()
        stopSession()

        val config = buildSessionConfig(role)

        var sessionRef: CyrinxTransportSession? = null
        val backend = AndroidAudioBackend(
            context = this,
            config = config,
            frameIngress = { frame, report ->
                sessionRef?.ingestFrame(frame, report) ?: CyrinxStatus.ERR_NOT_RUNNING
            },
            logSink = { appendLog(it) },
        )

        val session = CyrinxTransportSession(
            config = config,
            txSink = backend,
            log = { appendLog(it) },
        )
        sessionRef = session

        val backendRc = backend.start()
        if (backendRc != CyrinxStatus.OK) {
            appendLog("start failed: backend rc=$backendRc")
            statusText.text = "Status: Failed"
            return
        }

        val sessionRc = session.start()
        if (sessionRc != CyrinxStatus.OK) {
            backend.stop()
            appendLog("start failed: session rc=$sessionRc")
            statusText.text = "Status: Failed"
            return
        }

        this.backend = backend
        this.session = session

        statusText.text = "Status: Running (${role.name.lowercase(Locale.US)})"
        appendLog(
            "session started role=${role.name.lowercase(Locale.US)} sampleRate=${config.sampleRateHz} " +
                "band=${config.bandStartHz}...${config.bandEndHz} gain=${config.txGainCap} " +
                "dcss=${config.dcssSymbolSamples} sync=${config.preambleSyncThreshold}",
        )
    }

    private fun stopSession() {
        session?.stop()
        backend?.stop()
        session = null
        backend = null
        if (rawBackend == null) {
            statusText.text = "Status: Stopped"
        }
    }

    private fun sendProbe(bestEffort: Boolean) {
        val current = session ?: run {
            appendLog("send skipped: session not started")
            return
        }
        val payloadPrefix = if (bestEffort) "probe" else "probe-reliable"
        val payload = "$payloadPrefix:${System.currentTimeMillis()}".toByteArray(Charsets.UTF_8)
        ioExecutor.execute {
            val rc = sendProbeInternal(current, bestEffort, payload)
            appendLog("send ${if (bestEffort) "BE" else "reliable"} rc=$rc bytes=${payload.size}")
            refreshDiagnostics()
        }
    }

    private fun receiveOnce() {
        val current = session ?: run {
            appendLog("receive skipped: session not started")
            return
        }
        ioExecutor.execute {
            receiveOnceInternal(current)
            refreshDiagnostics()
        }
    }

    private fun startRawBackend(role: Role) {
        val micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (!micGranted) {
            appendLog("cannot start raw backend: RECORD_AUDIO permission missing")
            ensureMicPermission()
            return
        }

        stopSession()
        stopRawBackend()

        val config = buildSessionConfig(role)
        val backend = BasicToneAndroidBackend(
            context = this,
            config = config,
            frameIngress = { frame ->
                appendLog("raw rx bytes=${frame.size} text=${previewText(frame)} hex=${hexPrefix(frame)}")
            },
            capturePath = overrideRawCapturePath,
            logSink = { appendLog(it) },
        )

        val backendRc = backend.start()
        if (backendRc != CyrinxStatus.OK) {
            appendLog("raw start failed rc=$backendRc")
            statusText.text = "Status: Raw Failed"
            return
        }

        rawBackend = backend
        statusText.text = "Status: Raw Running (${role.name.lowercase(Locale.US)})"
        appendLog(
            "raw backend started role=${role.name.lowercase(Locale.US)} sampleRate=${config.sampleRateHz} " +
                "band=${config.bandStartHz}...${config.bandEndHz} gain=${config.txGainCap} " +
                "dcss=${config.dcssSymbolSamples} sync=${config.preambleSyncThreshold} rawCodec=${config.rawCodec} " +
                "capturePath=${overrideRawCapturePath ?: "none"}",
        )
    }

    private fun stopRawBackend() {
        rawBackend?.stop()
        rawBackend = null
        if (session == null) {
            statusText.text = "Status: Stopped"
        }
    }

    private fun sendRawText(text: String) {
        val current = rawBackend ?: run {
            appendLog("raw send skipped: backend not started")
            return
        }
        ioExecutor.execute {
            try {
                val payload = text.toByteArray(Charsets.UTF_8)
                appendLog("raw send begin bytes=${payload.size} text=$text")
                val rc = current.sendFrame(payload)
                appendLog("raw send rc=$rc bytes=${payload.size} text=$text")
                refreshDiagnostics()
            } catch (t: Throwable) {
                appendLog("raw send failed error=${t.javaClass.simpleName}:${t.message}")
            }
        }
    }

    private fun playBeacon() {
        val currentBackend = backend ?: run {
            appendLog("beacon skipped: session not started")
            return
        }
        ioExecutor.execute {
            val rc = currentBackend.playLocalAudibleBeacon(selectedRole())
            appendLog("beacon rc=$rc")
            refreshDiagnostics()
        }
    }

    private fun refreshDiagnostics() {
        val currentRawBackend = rawBackend
        if (currentRawBackend != null && session == null) {
            val backendDiag = currentRawBackend.diagnostics()
            val text =
                "backend=android-audio/raw state=${backendDiag.state} configuredHz=${backendDiag.configuredSampleRateHz} " +
                    "inHz=${backendDiag.observedInputSampleRateHz} outHz=${backendDiag.observedOutputSampleRateHz} " +
                    "txFrames=${backendDiag.txFrameCount} txBytes=${backendDiag.txByteCount} " +
                    "rxCallbacks=${backendDiag.rxCallbackCount} outCallbacks=${backendDiag.outputCallbackCount} " +
                    "pendingOutSamples=${backendDiag.pendingOutputSampleCount}"
            runOnUiThread {
                diagnosticsText.text = text
            }
            return
        }

        val currentBackend = backend
        val currentSession = session
        if (currentBackend == null || currentSession == null) {
            runOnUiThread {
                diagnosticsText.text = "Diagnostics unavailable"
            }
            return
        }

        val backendDiag = currentBackend.diagnostics()
        val metrics = currentSession.metrics()
        val text =
            "backend=android-audio state=${backendDiag.state} configuredHz=${backendDiag.configuredSampleRateHz} " +
                "inHz=${backendDiag.observedInputSampleRateHz} outHz=${backendDiag.observedOutputSampleRateHz} " +
                "txFrames=${backendDiag.txFrameCount} txBytes=${backendDiag.txByteCount} " +
                "rxCallbacks=${backendDiag.rxCallbackCount} outCallbacks=${backendDiag.outputCallbackCount} " +
                "pendingOutSamples=${backendDiag.pendingOutputSampleCount} coreGear=${metrics.currentGear} " +
                "coreTx=${metrics.txFrames} coreRx=${metrics.rxFrames} per2s=${"%.3f".format(Locale.US, metrics.per2s)}"

        runOnUiThread {
            diagnosticsText.text = text
        }
    }

    private fun appendLog(line: String) {
        val ts = SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date())
        val entry = "$ts $line"
        Log.i(TAG, entry)
        runOnUiThread {
            logLines.add(entry)
            if (logLines.size > 250) {
                logLines.subList(0, logLines.size - 250).clear()
            }
            logText.text = logLines.joinToString("\n")
        }
    }

    private fun handleAutomationIntent(intent: android.content.Intent?) {
        if (intent == null) {
            return
        }
        val cmd = intent.getStringExtra("cmd")?.trim()?.lowercase(Locale.US) ?: return
        val role = when (intent.getStringExtra("role")?.trim()?.lowercase(Locale.US)) {
            "master" -> Role.MASTER
            "slave" -> Role.SLAVE
            else -> null
        }
        if (role != null) {
            runOnUiThread { setRole(role) }
        }
        overrideSampleRateHz = intent.getIntExtra("sample_rate_hz", -1).takeIf { it > 0 } ?: overrideSampleRateHz
        overrideBandStartHz = intent.getIntExtra("band_start_hz", -1).takeIf { it > 0 } ?: overrideBandStartHz
        overrideBandEndHz = intent.getIntExtra("band_end_hz", -1).takeIf { it > 0 } ?: overrideBandEndHz
        overrideTxGainCap = intent.getFloatExtra("tx_gain", -1f).takeIf { it > 0f } ?: overrideTxGainCap
        overrideDcssSymbolSamples = intent.getIntExtra("dcss_symbol_samples", -1).takeIf { it > 0 } ?: overrideDcssSymbolSamples
        overrideSyncThreshold = intent.getFloatExtra("sync_threshold", -1f).takeIf { it > 0f } ?: overrideSyncThreshold
        overrideRawCodec = parseRawCodec(intent.getStringExtra("raw_codec")) ?: overrideRawCodec
        overrideRawCapturePath = intent.getStringExtra("capture_path")?.takeIf { it.isNotBlank() } ?: overrideRawCapturePath
        overrideChannels = intent.getIntExtra("channels", -1).takeIf { it > 0 } ?: overrideChannels
        overrideForceBodyMode = intent.getStringExtra("force_body_mode")?.takeIf { it.isNotBlank() } ?: overrideForceBodyMode

        appendLog(
            "automation cmd=$cmd role=${role?.name ?: "unchanged"} sampleRate=${overrideSampleRateHz ?: 48_000} " +
                "band=${overrideBandStartHz ?: 18_500}...${overrideBandEndHz ?: 21_000} " +
                "gain=${overrideTxGainCap ?: 0.70f} dcss=${overrideDcssSymbolSamples ?: 256} " +
                "sync=${overrideSyncThreshold ?: 0.25f} rawCodec=${overrideRawCodec ?: RawCodec.AUTO} " +
                "capturePath=${overrideRawCapturePath ?: "none"} channels=${overrideChannels ?: 1}",
        )
        when (cmd) {
            "vibrate" -> runOnUiThread {
                val durationMs = intent.getIntExtra("duration_ms", 1000).toLong()
                val vibrator = getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    vibrator.vibrate(VibrationEffect.createOneShot(durationMs, VibrationEffect.DEFAULT_AMPLITUDE))
                } else {
                    @Suppress("DEPRECATION")
                    vibrator.vibrate(durationMs)
                }
                appendLog("vibration triggered: ${durationMs}ms")
            }
            "start" -> runOnUiThread { startSession(role ?: selectedRole()) }
            "stop" -> runOnUiThread {
                stopSession()
                stopRawBackend()
            }
            "raw_start" -> runOnUiThread { startRawBackend(role ?: selectedRole()) }
            "raw_stop" -> runOnUiThread { stopRawBackend() }
            "raw_send_text" -> sendRawText(intent.getStringExtra("text") ?: "hello-from-android")
            "send_be" -> sendProbe(bestEffort = true)
            "send_rel" -> sendProbe(bestEffort = false)
            "receive" -> receiveOnce()
            "refresh" -> refreshDiagnostics()
            "beacon" -> playBeacon()
            "scenario" -> runAutomationScenario(role ?: selectedRole(), intent)
            "self_test" -> runPhySelfTest(role ?: selectedRole())
            "decode_file" -> runDecodeFile(role ?: selectedRole(), intent)
            "raw_encode_file" -> runRawEncodeFile(role ?: selectedRole(), intent)
            "raw_decode_file" -> runRawDecodeFile(role ?: selectedRole(), intent)
            "sweep" -> {
                val startHz = intent.getFloatExtra("start_hz", 1000f)
                val endHz = intent.getFloatExtra("end_hz", 22000f)
                val durationSec = intent.getFloatExtra("duration_sec", 8f)
                val amp = intent.getFloatExtra("amplitude", 0.35f)
                playSweep(startHz, endHz, durationSec, amp)
            }
            "rec_pcm" -> recPcmFile(intent)
            "play_pcm" -> playPcmFile(intent)
            "playrec_pcm" -> playRecPcmFile(intent)
            "bulk_decode" -> runBulkDecode(intent)
            else -> appendLog("unknown automation cmd=$cmd")
        }
    }

    private fun playSweep(startHz: Float, endHz: Float, durationSec: Float, amplitude: Float) {
        val sampleRate = 48000
        val count = (sampleRate * durationSec).toInt()
        val pcm = ShortArray(count)
        val fStart = startHz.toDouble()
        val fEnd = endHz.toDouble()
        val T = durationSec.toDouble()
        
        for (i in 0 until count) {
            val t = i.toDouble() / sampleRate.toDouble()
            val phase = 2.0 * Math.PI * (fStart * t + 0.5 * (fEnd - fStart) * (t * t) / T)
            val sample = amplitude * kotlin.math.sin(phase).toFloat()
            val clamped = sample.coerceIn(-1.0f, 1.0f)
            pcm[i] = (clamped * 32767.0f).toInt().toShort()
        }
        
        ioExecutor.execute {
            try {
                appendLog("sweep play begin: ${startHz}Hz to ${endHz}Hz dur=${durationSec}s")
                val minBuf = AudioTrack.getMinBufferSize(
                    sampleRate,
                    AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
                )
                val bufferSize = maxOf(minBuf, count * 2)
                val track = AudioTrack(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build(),
                    AudioFormat.Builder()
                        .setSampleRate(sampleRate)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build(),
                    bufferSize,
                    AudioTrack.MODE_STATIC,
                    AudioManager.AUDIO_SESSION_ID_GENERATE
                )
                track.write(pcm, 0, count)
                track.play()
                val playTimeMs = (durationSec * 1000).toLong()
                Thread.sleep(playTimeMs + 500)
                track.stop()
                track.release()
                appendLog("sweep play completed: ${startHz}Hz to ${endHz}Hz")
            } catch (t: Throwable) {
                appendLog("sweep play failed: ${t.message}")
            }
        }
    }


    private fun describeCaptureRoute(record: AudioRecord): String {
        val format = record.format
        val device = record.routedDevice
        val deviceDescription = if (device == null) {
            "none"
        } else {
            val rates = device.sampleRates.takeIf { it.isNotEmpty() }?.joinToString(",") ?: "unspecified"
            val counts = device.channelCounts.takeIf { it.isNotEmpty() }?.joinToString(",") ?: "unspecified"
            val masks = device.channelMasks.takeIf { it.isNotEmpty() }
                ?.joinToString(",") { "0x${it.toString(16)}" } ?: "unspecified"
            val indexMasks = device.channelIndexMasks.takeIf { it.isNotEmpty() }
                ?.joinToString(",") { "0x${it.toString(16)}" } ?: "unspecified"
            "type=${device.type} id=${device.id} address=${device.address} " +
                "product=${device.productName} rates=$rates counts=$counts " +
                "masks=$masks indexMasks=$indexMasks"
        }
        return "session=${record.audioSessionId} source=${record.audioSource} " +
            "rate=${format.sampleRate} channels=${format.channelCount} encoding=${format.encoding} " +
            "mask=0x${format.channelMask.toString(16)} indexMask=0x${format.channelIndexMask.toString(16)} " +
            "bufferFrames=${record.bufferSizeInFrames} device=[$deviceDescription]"
    }

    private fun describeChannelMapping(mapping: Int): String = when (mapping) {
        MicrophoneInfo.CHANNEL_MAPPING_DIRECT -> "direct"
        MicrophoneInfo.CHANNEL_MAPPING_PROCESSED -> "processed"
        else -> "unknown-$mapping"
    }

    private fun captureSource(sourceName: String): Int = when (sourceName) {
        "mic" -> MediaRecorder.AudioSource.MIC
        "voice_recognition" -> MediaRecorder.AudioSource.VOICE_RECOGNITION
        "camcorder" -> MediaRecorder.AudioSource.CAMCORDER
        else -> MediaRecorder.AudioSource.UNPROCESSED
    }

    private data class ActiveMicrophoneSnapshot(
        val microphones: List<MicrophoneInfo>,
        val ready: Boolean,
        val waitedMs: Long,
    )

    private fun hasDistinctDirectChannelMappings(
        microphones: List<MicrophoneInfo>,
        channels: Int,
    ): Boolean {
        if (channels <= 0) return false
        val ownersByChannel = Array(channels) { linkedSetOf<Int>() }
        microphones.forEach { microphone ->
            microphone.channelMapping.forEach { mapping ->
                val channel = mapping.first
                if (
                    channel in 0 until channels &&
                    mapping.second == MicrophoneInfo.CHANNEL_MAPPING_DIRECT
                ) {
                    ownersByChannel[channel].add(microphone.id)
                }
            }
        }
        if (ownersByChannel.any { it.isEmpty() }) return false
        return channels < 2 || ownersByChannel[0].any { it !in ownersByChannel[1] }
    }

    private fun awaitActiveMicrophones(
        record: AudioRecord,
        channels: Int,
        requireDistinctDirectMappings: Boolean,
        timeoutMs: Long = 400,
    ): ActiveMicrophoneSnapshot {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            return ActiveMicrophoneSnapshot(emptyList(), false, 0)
        }
        val startedMs = android.os.SystemClock.elapsedRealtime()
        var microphones = record.activeMicrophones
        fun isReady(): Boolean = if (requireDistinctDirectMappings) {
            hasDistinctDirectChannelMappings(microphones, channels)
        } else {
            microphones.isNotEmpty()
        }
        while (!isReady() && android.os.SystemClock.elapsedRealtime() - startedMs < timeoutMs) {
            Thread.sleep(20)
            microphones = record.activeMicrophones
        }
        val waitedMs = android.os.SystemClock.elapsedRealtime() - startedMs
        return ActiveMicrophoneSnapshot(microphones, isReady(), waitedMs)
    }

    private fun logActiveMicrophones(
        record: AudioRecord,
        command: String,
        requestId: String,
        phase: String,
        snapshot: List<MicrophoneInfo>? = null,
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            appendLog(
                "$command microphones request=$requestId phase=$phase " +
                    "unavailable api=${Build.VERSION.SDK_INT}",
            )
            return
        }
        try {
            val microphones = snapshot ?: record.activeMicrophones
            appendLog(
                "$command microphones request=$requestId phase=$phase count=${microphones.size}",
            )
            microphones.forEachIndexed { listIndex, microphone ->
                val mapping = microphone.channelMapping.joinToString(",") {
                    "${it.first}:${describeChannelMapping(it.second)}"
                }.ifEmpty { "none" }
                val position = microphone.position
                val orientation = microphone.orientation
                val response = microphone.frequencyResponse
                val responseSummary = if (response.isEmpty()) {
                    "none"
                } else {
                    "points=${response.size} first=${response.first().first}:${response.first().second} " +
                        "last=${response.last().first}:${response.last().second}"
                }
                appendLog(
                    "$command microphone request=$requestId phase=$phase " +
                        "listIndex=$listIndex id=${microphone.id} " +
                        "description=${microphone.description} address=${microphone.address} " +
                        "type=${microphone.type} location=${microphone.location} " +
                        "group=${microphone.group} groupIndex=${microphone.indexInTheGroup} " +
                        "directionality=${microphone.directionality} mapping=$mapping " +
                        "position=${position.x},${position.y},${position.z} " +
                        "orientation=${orientation.x},${orientation.y},${orientation.z} " +
                        "sensitivity=${microphone.sensitivity} " +
                        "spl=${microphone.minSpl}...${microphone.maxSpl} " +
                        "response=[$responseSummary]",
                )
            }
        } catch (t: Throwable) {
            appendLog(
                "$command microphones request=$requestId phase=$phase " +
                    "failed=${t.javaClass.simpleName}:${t.message}",
            )
        }
    }

    // Raw PCM16LE mic capture to the app's internal files dir (pull via `adb exec-out run-as ... cat`).
    // Used by the Mac-side channel measurement / modem iteration harness, which does all DSP offline.
    private fun recPcmFile(intent: android.content.Intent) {
        val durationSec = intent.getFloatExtra("duration_sec", 5f).coerceIn(0.5f, 600f)
        val sampleRate = intent.getIntExtra("sample_rate_hz", 48_000)
        val channels = intent.getIntExtra("channels", 2).coerceIn(1, 2)
        val sourceName = intent.getStringExtra("source")?.trim()?.lowercase(Locale.US) ?: "unprocessed"
        val outName = intent.getStringExtra("out_name")?.takeIf { it.isNotBlank() } ?: "cap.pcm"
        val requestId = intent.getStringExtra("request_id")?.takeIf { it.isNotBlank() } ?: outName
        ioExecutor.execute {
            var record: AudioRecord? = null
            try {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
                    PackageManager.PERMISSION_GRANTED
                ) {
                    appendLog("rec_pcm failed: request=$requestId RECORD_AUDIO not granted")
                    return@execute
                }
                val source = captureSource(sourceName)
                val chMask = if (channels == 2) AudioFormat.CHANNEL_IN_STEREO else AudioFormat.CHANNEL_IN_MONO
                val minBuf = AudioRecord.getMinBufferSize(sampleRate, chMask, AudioFormat.ENCODING_PCM_16BIT)
                record = AudioRecord(
                    source,
                    sampleRate,
                    chMask,
                    AudioFormat.ENCODING_PCM_16BIT,
                    maxOf(minBuf * 4, sampleRate * channels),
                )
                if (record.state != AudioRecord.STATE_INITIALIZED) {
                    appendLog(
                        "rec_pcm failed: request=$requestId AudioRecord init failed " +
                            "src=$sourceName rate=$sampleRate ch=$channels",
                    )
                    return@execute
                }
                val actualRate = record.sampleRate
                val actualCh = record.channelCount
                val totalFrames = (durationSec * actualRate).toInt()
                val out = File(filesDir, outName)
                android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)
                val buf = ShortArray(4096 * actualCh)
                val bb = ByteBuffer.allocate(buf.size * 2).order(ByteOrder.LITTLE_ENDIAN)
                var framesRead = 0
                var clipped = 0
                var sumSq = 0.0
                record.startRecording()
                if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                    throw IllegalStateException("AudioRecord did not enter RECORDSTATE_RECORDING")
                }
                val requiresDistinctDirectMappings =
                    source == MediaRecorder.AudioSource.UNPROCESSED && actualCh == 2
                val startMicrophones = awaitActiveMicrophones(
                    record,
                    actualCh,
                    requiresDistinctDirectMappings,
                )
                appendLog(
                    "rec_pcm microphone_stabilization request=$requestId phase=start " +
                        "requiredDistinctDirect=$requiresDistinctDirectMappings " +
                        "ready=${startMicrophones.ready} waitedMs=${startMicrophones.waitedMs}",
                )
                appendLog(
                    "rec_pcm route request=$requestId phase=start ${describeCaptureRoute(record)}",
                )
                logActiveMicrophones(
                    record,
                    "rec_pcm",
                    requestId,
                    "start",
                    startMicrophones.microphones,
                )
                appendLog(
                    "rec_pcm begin: request=$requestId src=$sourceName rate=$actualRate " +
                        "ch=$actualCh frames=$totalFrames -> ${out.absolutePath}",
                )
                java.io.BufferedOutputStream(java.io.FileOutputStream(out), 1 shl 20).use { fos ->
                    while (framesRead < totalFrames) {
                        val want = minOf(buf.size, (totalFrames - framesRead) * actualCh)
                        val n = record.read(buf, 0, want)
                        if (n <= 0) {
                            appendLog("rec_pcm read_error: request=$requestId n=$n")
                            break
                        }
                        bb.clear()
                        for (i in 0 until n) {
                            val s = buf[i]
                            bb.putShort(s)
                            val f = s / 32768.0
                            sumSq += f * f
                            if (s >= 32766 || s <= -32767) clipped += 1
                        }
                        fos.write(bb.array(), 0, n * 2)
                        framesRead += n / actualCh
                    }
                }
                appendLog(
                    "rec_pcm route request=$requestId phase=end ${describeCaptureRoute(record)}",
                )
                logActiveMicrophones(record, "rec_pcm", requestId, "end")
                record.stop()
                val rms = kotlin.math.sqrt(sumSq / maxOf(1, framesRead * actualCh))
                appendLog(
                    "rec_pcm done: request=$requestId out=$outName frames=$framesRead " +
                        "rms=${"%.6f".format(rms)} clipped=$clipped bytes=${out.length()}",
                )
            } catch (t: Throwable) {
                appendLog(
                    "rec_pcm failed: request=$requestId ${t.javaClass.simpleName}: ${t.message}",
                )
            } finally {
                record?.release()
            }
        }
    }

    // Full-duplex raw PCM for same-device speaker-to-microphone characterization.
    // The transmit file must contain its own sync markers; host and audio clocks are not aligned.
    private fun playRecPcmFile(intent: android.content.Intent) {
        val txPath = intent.getStringExtra("path")?.takeIf { it.isNotBlank() }
            ?: "/data/local/tmp/tx.pcm"
        val outName = intent.getStringExtra("out_name")?.takeIf { it.isNotBlank() }
            ?: "playrec-cap.pcm"
        val sampleRate = intent.getIntExtra("sample_rate_hz", 48_000)
        val inputChannels = intent.getIntExtra("input_channels", 2).coerceIn(1, 2)
        val outputChannels = intent.getIntExtra("output_channels", 2).coerceIn(1, 2)
        val preRollMs = intent.getIntExtra("pre_roll_ms", 500).coerceIn(100, 10_000)
        val postRollMs = intent.getIntExtra("post_roll_ms", 750).coerceIn(100, 10_000)
        val sourceName = intent.getStringExtra("source")?.trim()?.lowercase(Locale.US)
            ?: "unprocessed"
        val requestId = intent.getStringExtra("request_id")?.takeIf { it.isNotBlank() }
            ?: outName
        ioExecutor.execute {
            var record: AudioRecord? = null
            var track: AudioTrack? = null
            var captureThread: Thread? = null
            try {
                if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
                    PackageManager.PERMISSION_GRANTED
                ) {
                    appendLog("playrec_pcm failed: request=$requestId RECORD_AUDIO not granted")
                    return@execute
                }
                val txData = File(txPath).readBytes()
                val txBytesPerFrame = outputChannels * 2
                if (txData.isEmpty() || txData.size % txBytesPerFrame != 0) {
                    appendLog(
                        "playrec_pcm failed: request=$requestId transmit bytes=${txData.size} " +
                            "are not aligned to " +
                            "$outputChannels-channel PCM16 frames",
                    )
                    return@execute
                }
                val txFrames = txData.size / txBytesPerFrame
                val inputMask = if (inputChannels == 2) {
                    AudioFormat.CHANNEL_IN_STEREO
                } else {
                    AudioFormat.CHANNEL_IN_MONO
                }
                val outputMask = if (outputChannels == 2) {
                    AudioFormat.CHANNEL_OUT_STEREO
                } else {
                    AudioFormat.CHANNEL_OUT_MONO
                }
                val inputMinBytes = AudioRecord.getMinBufferSize(
                    sampleRate,
                    inputMask,
                    AudioFormat.ENCODING_PCM_16BIT,
                )
                val outputMinBytes = AudioTrack.getMinBufferSize(
                    sampleRate,
                    outputMask,
                    AudioFormat.ENCODING_PCM_16BIT,
                )
                if (inputMinBytes <= 0 || outputMinBytes <= 0) {
                    appendLog(
                        "playrec_pcm failed: request=$requestId unsupported buffers " +
                            "in=$inputMinBytes out=$outputMinBytes",
                    )
                    return@execute
                }
                val recorder = AudioRecord(
                    captureSource(sourceName),
                    sampleRate,
                    inputMask,
                    AudioFormat.ENCODING_PCM_16BIT,
                    maxOf(inputMinBytes * 4, sampleRate * inputChannels),
                )
                record = recorder
                val player = AudioTrack(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build(),
                    AudioFormat.Builder()
                        .setSampleRate(sampleRate)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(outputMask)
                        .build(),
                    maxOf(outputMinBytes, txData.size),
                    AudioTrack.MODE_STATIC,
                    AudioManager.AUDIO_SESSION_ID_GENERATE,
                )
                track = player
                if (recorder.state != AudioRecord.STATE_INITIALIZED ||
                    player.state != AudioTrack.STATE_INITIALIZED
                ) {
                    appendLog(
                        "playrec_pcm failed: request=$requestId init " +
                            "record=${recorder.state} track=${player.state}",
                    )
                    return@execute
                }
                val written = player.write(txData, 0, txData.size)
                if (written != txData.size) {
                    appendLog(
                        "playrec_pcm failed: request=$requestId static write=$written " +
                            "expected=${txData.size}",
                    )
                    return@execute
                }
                val actualRate = recorder.sampleRate
                val actualInputChannels = recorder.channelCount
                val captureFrames =
                    ((preRollMs + postRollMs) * actualRate / 1_000L).toInt() + txFrames
                val out = File(filesDir, outName)
                val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
                val mediaVolume = audioManager.getStreamVolume(AudioManager.STREAM_MUSIC)
                val mediaVolumeMax = audioManager.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
                appendLog(
                    "playrec_pcm begin: request=$requestId src=$sourceName rate=$actualRate " +
                        "inCh=$actualInputChannels " +
                        "outCh=$outputChannels txFrames=$txFrames captureFrames=$captureFrames " +
                        "preMs=$preRollMs postMs=$postRollMs volume=$mediaVolume/$mediaVolumeMax " +
                        "-> ${out.absolutePath}",
                )

                val captureError = java.util.concurrent.atomic.AtomicReference<Throwable?>(null)
                var framesRead = 0
                var clipped = 0
                var sumSq = 0.0
                var equalStereoFrames = 0
                val worker = Thread({
                    try {
                        android.os.Process.setThreadPriority(
                            android.os.Process.THREAD_PRIORITY_URGENT_AUDIO,
                        )
                        val buffer = ShortArray(4_096 * actualInputChannels)
                        val bytes = ByteBuffer.allocate(buffer.size * 2).order(ByteOrder.LITTLE_ENDIAN)
                        java.io.BufferedOutputStream(
                            java.io.FileOutputStream(out),
                            1 shl 20,
                        ).use { stream ->
                            while (framesRead < captureFrames) {
                                val wanted = minOf(
                                    buffer.size,
                                    (captureFrames - framesRead) * actualInputChannels,
                                )
                                val count = recorder.read(
                                    buffer,
                                    0,
                                    wanted,
                                    AudioRecord.READ_BLOCKING,
                                )
                                if (count <= 0) {
                                    throw IllegalStateException("AudioRecord.read returned $count")
                                }
                                bytes.clear()
                                for (index in 0 until count) {
                                    val sample = buffer[index]
                                    bytes.putShort(sample)
                                    val normalized = sample / 32768.0
                                    sumSq += normalized * normalized
                                    if (sample >= 32766 || sample <= -32767) clipped += 1
                                }
                                if (actualInputChannels == 2) {
                                    for (index in 0 until count / 2) {
                                        if (buffer[index * 2] == buffer[index * 2 + 1]) {
                                            equalStereoFrames += 1
                                        }
                                    }
                                }
                                stream.write(bytes.array(), 0, count * 2)
                                framesRead += count / actualInputChannels
                            }
                        }
                    } catch (t: Throwable) {
                        captureError.set(t)
                    }
                }, "cyrinx-playrec-capture")
                captureThread = worker

                recorder.startRecording()
                if (recorder.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                    throw IllegalStateException("AudioRecord did not enter RECORDSTATE_RECORDING")
                }
                val recordStartNanos = android.os.SystemClock.elapsedRealtimeNanos()
                worker.start()
                appendLog(
                    "playrec_pcm route request=$requestId phase=start " +
                        describeCaptureRoute(recorder),
                )
                logActiveMicrophones(recorder, "playrec_pcm", requestId, "start")
                Thread.sleep(preRollMs.toLong())
                val playCallNanos = android.os.SystemClock.elapsedRealtimeNanos()
                player.play()
                val playbackDeadline = android.os.SystemClock.elapsedRealtime() +
                    (txFrames * 1_000L / sampleRate) + 5_000L
                while (player.playbackHeadPosition.toLong() < txFrames &&
                    android.os.SystemClock.elapsedRealtime() < playbackDeadline
                ) {
                    Thread.sleep(5)
                }
                if (player.playbackHeadPosition.toLong() < txFrames) {
                    throw IllegalStateException(
                        "playback timeout head=${player.playbackHeadPosition} expected=$txFrames",
                    )
                }
                val trackTimestamp = android.media.AudioTimestamp()
                val hasTrackTimestamp = player.getTimestamp(trackTimestamp)
                player.stop()
                worker.join(postRollMs.toLong() + 5_000L)
                if (worker.isAlive) {
                    recorder.stop()
                    worker.join(1_000)
                    throw IllegalStateException("capture thread did not finish")
                }
                captureError.get()?.let { throw it }

                appendLog(
                    "playrec_pcm route request=$requestId phase=end ${describeCaptureRoute(recorder)}",
                )
                logActiveMicrophones(recorder, "playrec_pcm", requestId, "end")
                val recordTimestamp = android.media.AudioTimestamp()
                val recordTimestampStatus = recorder.getTimestamp(
                    recordTimestamp,
                    android.media.AudioTimestamp.TIMEBASE_MONOTONIC,
                )
                recorder.stop()
                val rms = kotlin.math.sqrt(
                    sumSq / maxOf(1, framesRead * actualInputChannels),
                )
                val equalFraction = if (actualInputChannels == 2 && framesRead > 0) {
                    equalStereoFrames.toDouble() / framesRead
                } else {
                    Double.NaN
                }
                appendLog(
                    "playrec_pcm timing request=$requestId recordStartNs=$recordStartNanos " +
                        "playCallNs=$playCallNanos " +
                        "recordTsStatus=$recordTimestampStatus " +
                        "recordFrame=${recordTimestamp.framePosition} " +
                        "recordTsNs=${recordTimestamp.nanoTime} trackTs=$hasTrackTimestamp " +
                        "trackFrame=${trackTimestamp.framePosition} trackTsNs=${trackTimestamp.nanoTime}",
                )
                appendLog(
                    "playrec_pcm done: request=$requestId out=$outName frames=$framesRead " +
                        "rms=${"%.6f".format(rms)} " +
                        "clipped=$clipped equalStereoFraction=$equalFraction bytes=${out.length()}",
                )
            } catch (t: Throwable) {
                appendLog(
                    "playrec_pcm failed: request=$requestId ${t.javaClass.simpleName}: ${t.message}",
                )
            } finally {
                try {
                    track?.stop()
                } catch (_: Throwable) {
                }
                try {
                    record?.stop()
                } catch (_: Throwable) {
                }
                val worker = captureThread
                if (worker != null && worker.isAlive && worker !== Thread.currentThread()) {
                    try {
                        worker.join(2_000)
                    } catch (_: InterruptedException) {
                        Thread.currentThread().interrupt()
                    }
                }
                if (worker?.isAlive == true) {
                    appendLog(
                        "playrec_pcm cleanup_failed: request=$requestId capture thread alive",
                    )
                } else {
                    record?.release()
                }
                track?.release()
            }
        }
    }

    // Plays raw PCM16LE pushed through adb. Volume changes require an explicit max_volume request.
    private fun playPcmFile(intent: android.content.Intent) {
        val path = intent.getStringExtra("path")?.takeIf { it.isNotBlank() } ?: "/data/local/tmp/tx.pcm"
        val sampleRate = intent.getIntExtra("sample_rate_hz", 48_000)
        val channels = intent.getIntExtra("channels", 1).coerceIn(1, 2)
        val maxVolume = intent.getIntExtra("max_volume", 0)
        val requestId = intent.getStringExtra("request_id")?.takeIf { it.isNotBlank() }
            ?: "unspecified"
        ioExecutor.execute {
            try {
                val data = File(path).readBytes()
                if (maxVolume != 0) {
                    val am = getSystemService(Context.AUDIO_SERVICE) as AudioManager
                    val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
                    am.setStreamVolume(AudioManager.STREAM_MUSIC, max, 0)
                    appendLog("play_pcm volume request=$requestId set=$max/$max")
                }
                val chMask = if (channels == 2) AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO
                val bytesPerFrame = 2 * channels
                val frames = data.size / bytesPerFrame
                appendLog(
                    "play_pcm begin: request=$requestId path=$path bytes=${data.size} " +
                        "frames=$frames rate=$sampleRate ch=$channels",
                )
                val minBuf = AudioTrack.getMinBufferSize(sampleRate, chMask, AudioFormat.ENCODING_PCM_16BIT)
                val track = AudioTrack(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build(),
                    AudioFormat.Builder()
                        .setSampleRate(sampleRate)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(chMask)
                        .build(),
                    maxOf(minBuf * 4, 1 shl 16),
                    AudioTrack.MODE_STREAM,
                    AudioManager.AUDIO_SESSION_ID_GENERATE,
                )
                track.play()
                var off = 0
                while (off < data.size) {
                    val n = track.write(data, off, minOf(1 shl 16, data.size - off))
                    if (n <= 0) {
                        appendLog("play_pcm write_error: request=$requestId n=$n")
                        break
                    }
                    off += n
                }
                // Drain whatever is still buffered before stopping (buffer is at most ~minBuf*4 bytes).
                val drainMs = ((minBuf * 4L / bytesPerFrame) * 1000L / sampleRate) + 250L
                Thread.sleep(drainMs)
                track.stop()
                track.release()
                appendLog("play_pcm done: request=$requestId wrote=$off bytes")
            } catch (t: Throwable) {
                appendLog(
                    "play_pcm failed: request=$requestId ${t.javaClass.simpleName}: ${t.message}",
                )
            }
        }
    }

    // Legacy on-device demodulation of a wideband bulk-PHY capture (see BulkDemod.kt).
    // The phone verifies decoded payload bytes against the transmitter's DetRng
    // PRBS. A headline score additionally requires a payload-independent schedule
    // origin supplied by the harness; without one, only diagnostic counts are logged.
    private fun runBulkDecode(intent: android.content.Intent) {
        val path = intent.getStringExtra("path")?.takeIf { it.isNotBlank() }
            ?: File(filesDir, "cap.pcm").absolutePath
        val requestId = intent.getStringExtra("request_id")?.takeIf { it.isNotBlank() }
            ?: "unspecified"
        val channels = intent.getIntExtra("channels", 2)
        val fLo = intent.getFloatExtra("f_lo", 1100f).toDouble()
        val fHi = intent.getFloatExtra("f_hi", 23000f).toDouble()
        val nSym = intent.getIntExtra("n_sym", 64)
        val nPayloads = intent.getIntExtra("n_payloads", 3)
        val seedBase = intent.getIntExtra("payload_seed_base", 1000).toLong()
        val gapSamples = intent.getIntExtra("gap_samples", BulkDemod.SRATE / 4).coerceAtLeast(0)
        val trailingPadSamples =
            intent.getIntExtra("trailing_pad_samples", BulkDemod.SRATE / 3).coerceAtLeast(0)
        val scheduleOriginSample =
            intent.getIntExtra("schedule_origin_sample", -1).takeIf { it >= 0 }
        val slotToleranceSamples =
            intent.getIntExtra("slot_tolerance_samples", BulkDemod.SRATE / 100).coerceAtLeast(0)
        ioExecutor.execute {
            try {
                val t0 = System.currentTimeMillis()
                val cfg = BulkDemod.Cfg(fLo, fHi, nSym)
                val results = BulkDemod.decodeCapture(
                    path, channels, fLo, fHi, nSym, nPayloads, seedBase,
                    scheduleOriginSample, gapSamples, slotToleranceSamples,
                ) { appendLog("bulk_decode request=$requestId $it") }
                val verified = results.sumOf { it.verified }
                val metrics = BulkMeasurement.score(
                    verifiedBlocks = verified,
                    blocksPerFrame = cfg.nBlocks,
                    frameCount = nPayloads,
                    frameSamples = cfg.frameSamples,
                    gapSamples = gapSamples,
                    trailingPadSamples = trailingPadSamples,
                    sampleRate = BulkDemod.SRATE,
                )
                val attributedFrames = results.mapNotNull { it.attributedFrame }.distinct().size
                val diagnosticVerified = results.sumOf { it.diagnosticVerified }
                val diagnosticFrames =
                    results.mapNotNull { it.diagnosticAttributedFrame }.distinct().size
                val spanS = metrics.scheduledSpanSamples.toDouble() / BulkDemod.SRATE
                val grossSpanS = metrics.grossSpanSamples.toDouble() / BulkDemod.SRATE
                if (scheduleOriginSample == null) {
                    appendLog(
                        "bulk_decode TOTAL: request=$requestId headline_valid=false " +
                            "verified=0/${metrics.totalBlocks} blocks " +
                            "frames=0/$nPayloads span=${"%.2f".format(spanS)}s goodput=REFUSED " +
                            "gross_span=${"%.2f".format(grossSpanS)}s gross_goodput=REFUSED " +
                            "diagnostic_verified=$diagnosticVerified/${metrics.totalBlocks} " +
                            "diagnostic_frames=$diagnosticFrames/$nPayloads " +
                            "reason=missing_schedule_origin_sample " +
                            "wall_ms=${System.currentTimeMillis() - t0}",
                    )
                } else {
                    appendLog(
                        "bulk_decode TOTAL: request=$requestId headline_valid=true " +
                            "verified=$verified/${metrics.totalBlocks} blocks " +
                            "(${verified * BulkDemod.CRC_BLOCK} bytes) " +
                            "frames=$attributedFrames/$nPayloads span=${"%.2f".format(spanS)}s " +
                            "goodput=${"%.0f".format(metrics.scheduledGoodputBps)} bps " +
                            "gross_span=${"%.2f".format(grossSpanS)}s " +
                            "gross_goodput=${"%.0f".format(metrics.grossGoodputBps)} bps " +
                            "diagnostic_verified=$diagnosticVerified/${metrics.totalBlocks} " +
                            "origin_sample=$scheduleOriginSample tolerance_samples=$slotToleranceSamples " +
                            "wall_ms=${System.currentTimeMillis() - t0}",
                    )
                }
            } catch (t: Throwable) {
                appendLog(
                    "bulk_decode failed: request=$requestId ${t.javaClass.simpleName}: ${t.message}",
                )
            }
        }
    }

    private fun runAutomationScenario(role: Role, intent: android.content.Intent) {
        val durationSec = intent.getIntExtra("duration_sec", 15).coerceIn(5, 180)
        val intervalMs = intent.getIntExtra("send_interval_ms", 750).coerceIn(20, 5_000)
        val reliableEvery = intent.getIntExtra("reliable_every", 4)
        val payloadSize = intent.getIntExtra("payload_size", 24).coerceIn(20, 1024)

        runOnUiThread { startSession(role) }

        ioExecutor.execute {
            val stopAt = System.currentTimeMillis() + (durationSec * 1_000L)
            var counter = 0
            val current = session
            if (current == null) {
                appendLog("scenario aborted: session not started")
                return@execute
            }
            while (System.currentTimeMillis() < stopAt) {
                val prefixBe = "probe:${System.currentTimeMillis()}:"
                val prefixBeBytes = prefixBe.toByteArray(Charsets.UTF_8)
                val bePayload = ByteArray(payloadSize)
                System.arraycopy(prefixBeBytes, 0, bePayload, 0, minOf(prefixBeBytes.size, payloadSize))
                if (payloadSize > prefixBeBytes.size) {
                    for (i in prefixBeBytes.size until payloadSize) {
                        bePayload[i] = ((i * 17) and 0xFF).toByte()
                    }
                }

                val beRc = sendProbeInternal(current, bestEffort = true, payload = bePayload)
                appendLog("scenario send BE rc=$beRc")
                if (reliableEvery > 0 && counter % reliableEvery == 0) {
                    val prefixRel = "probe-reliable:${System.currentTimeMillis()}:"
                    val prefixRelBytes = prefixRel.toByteArray(Charsets.UTF_8)
                    val relPayload = ByteArray(payloadSize)
                    System.arraycopy(prefixRelBytes, 0, relPayload, 0, minOf(prefixRelBytes.size, payloadSize))
                    if (payloadSize > prefixRelBytes.size) {
                        for (i in prefixRelBytes.size until payloadSize) {
                            relPayload[i] = ((i * 17) and 0xFF).toByte()
                        }
                    }
                    val relRc = sendProbeInternal(current, bestEffort = false, payload = relPayload)
                    appendLog("scenario send reliable rc=$relRc")
                }
                receiveOnceInternal(current)
                Thread.sleep(intervalMs.toLong())
                counter += 1
            }
            appendLog("scenario finished durationSec=$durationSec")
        }
    }

    private fun sendProbeInternal(current: CyrinxTransportSession, bestEffort: Boolean, payload: ByteArray): Int {
        return current.send(
            data = payload,
            streamId = if (bestEffort) 7 else 9,
            qos = if (bestEffort) QoS.BEST_EFFORT else QoS.RELIABLE,
            priority = 2,
            streamFlags = CyrinxConstants.STREAM_FLAG_FIN,
        )
    }

    private fun receiveOnceInternal(current: CyrinxTransportSession) {
        val msg = current.receive(timeoutMs = 100)
        if (msg == null) {
            appendLog("rx timeout")
        } else {
            val preview = msg.data.decodeToString().take(64)
            appendLog("rx stream=${msg.streamId} bytes=${msg.data.size} preview=$preview")
        }
    }

    private fun buildSessionConfig(role: Role): SessionConfig {
        return SessionConfig(
            role = role,
            sampleRateHz = overrideSampleRateHz ?: 48_000,
            bandStartHz = overrideBandStartHz ?: 18_500,
            bandEndHz = overrideBandEndHz ?: 21_000,
            txGainCap = overrideTxGainCap ?: 0.70f,
            dcssSymbolSamples = overrideDcssSymbolSamples ?: 256,
            preambleSyncThreshold = overrideSyncThreshold ?: 0.25f,
            rawCodec = overrideRawCodec ?: RawCodec.AUTO,
            channels = overrideChannels ?: 2,
            deviceSignature = 0x02, // CYRINX_DEVICE_PIXEL_7A
            maxBufferCapacity = 65536,
            forceBodyMode = overrideForceBodyMode,
        )
    }

    private fun parseRawCodec(raw: String?): RawCodec? {
        return when (raw?.trim()?.lowercase(Locale.US)) {
            "auto" -> RawCodec.AUTO
            "basic" -> RawCodec.BASIC
            "reverse", "reverse_burst", "reverse-burst" -> RawCodec.REVERSE_BURST
            "ook" -> RawCodec.OOK
            "morse" -> RawCodec.MORSE
            "dtmf" -> RawCodec.DTMF
            "nibble" -> RawCodec.NIBBLE
            else -> null
        }
    }

    private fun previewText(bytes: ByteArray): String {
        return bytes.toString(Charsets.UTF_8)
            .map { ch -> if (ch.isISOControl()) '.' else ch }
            .joinToString("")
            .take(64)
    }

    private fun hexPrefix(bytes: ByteArray, count: Int = 8): String {
        return bytes.take(count).joinToString("") { "%02x".format(it) }
    }

    private fun runPhySelfTest(role: Role) {
        ioExecutor.execute {
            val cfg = buildSessionConfig(role)
            val phy = AcousticPhyLink(cfg)

            // Test DCSS Robust Mode (Gear 0)
            val dcssFrame = ByteArray(48) { idx -> ((idx * 13) and 0xFF).toByte() }
            dcssFrame[2] = (dcssFrame[2].toInt() and 0xF0).toByte() // frameType = 0
            dcssFrame[10] = 0 // gearId = 0
            val encodedDcss = phy.encode(dcssFrame)
            val decodedDcss = phy.ingest(encodedDcss)
            val dcssOk = decodedDcss.firstOrNull()?.frame?.contentEquals(dcssFrame) == true

            // Test OFDM Turbo Mode (Gear 3)
            val ofdmFrame = ByteArray(48) { idx -> ((idx * 17) and 0xFF).toByte() }
            ofdmFrame[2] = (ofdmFrame[2].toInt() and 0xF0).toByte() // frameType = 0
            ofdmFrame[10] = (3 shl 5).toByte() // gearId = 3
            val encodedOfdm = phy.encode(ofdmFrame)
            val decodedOfdm = phy.ingest(encodedOfdm)
            val ofdmOk = decodedOfdm.firstOrNull()?.frame?.contentEquals(ofdmFrame) == true

            val allOk = dcssOk && ofdmOk
            appendLog(
                "self_test ok=$allOk dcssOk=$dcssOk (samples=${encodedDcss.size}) ofdmOk=$ofdmOk (samples=${encodedOfdm.size}) " +
                    "sampleRate=${cfg.sampleRateHz} band=${cfg.bandStartHz}...${cfg.bandEndHz}",
            )
        }
    }

    private fun runDecodeFile(role: Role, intent: android.content.Intent) {
        ioExecutor.execute {
            val cfg = buildSessionConfig(role)
            val wavePath = intent.getStringExtra("wave_path")?.takeIf { it.isNotBlank() } ?: "/data/local/tmp/cyrinx_wave_f32le.bin"
            val file = File(wavePath)
            if (!file.exists()) {
                appendLog("decode_file missing path=$wavePath")
                return@execute
            }
            val bytes = file.readBytes()
            if (bytes.size < 4 || (bytes.size % 4) != 0) {
                appendLog("decode_file invalid byteCount=${bytes.size}")
                return@execute
            }
            val sampleCount = bytes.size / 4
            val samples = FloatArray(sampleCount)
            val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
            for (i in 0 until sampleCount) {
                samples[i] = buf.float
            }

            val phy = AcousticPhyLink(cfg)
            val decoded = phy.ingest(samples)
            val first = decoded.firstOrNull()
            val firstLen = first?.frame?.size ?: 0
            val firstPrefix = first?.frame?.take(8)?.joinToString("") { "%02x".format(it) } ?: ""
            appendLog(
                "decode_file path=$wavePath samples=$sampleCount decodedFrames=${decoded.size} " +
                    "firstLen=$firstLen firstPrefix=$firstPrefix",
            )
        }
    }

    private fun runRawEncodeFile(role: Role, intent: android.content.Intent) {
        ioExecutor.execute {
            val cfg = buildSessionConfig(role)
            val wavePath = intent.getStringExtra("wave_path")?.takeIf { it.isNotBlank() }
                ?: "/sdcard/Download/cyrinx_raw_encode_f32le.bin"
            val text = intent.getStringExtra("text") ?: "hello-from-android"
            val payload = text.toByteArray(Charsets.UTF_8)
            val waveform = encodeRawWaveform(cfg, payload)
            writeFloat32LE(wavePath, waveform)
            val decoded = decodeRawWaveform(cfg, waveform)
            val first = decoded.firstOrNull()
            appendLog(
                "raw_encode_file path=$wavePath codec=${resolvedRawCodec(cfg)} samples=${waveform.size} " +
                    "bytes=${payload.size} decodedFrames=${decoded.size} " +
                    "firstText=${first?.let { previewText(it) } ?: ""} firstHex=${first?.let { hexPrefix(it) } ?: ""}",
            )
        }
    }

    private fun runRawDecodeFile(role: Role, intent: android.content.Intent) {
        ioExecutor.execute {
            val cfg = buildSessionConfig(role)
            val wavePath = intent.getStringExtra("wave_path")?.takeIf { it.isNotBlank() }
                ?: "/sdcard/Download/cyrinx_raw_encode_f32le.bin"
            val file = File(wavePath)
            if (!file.exists()) {
                appendLog("raw_decode_file missing path=$wavePath")
                return@execute
            }
            val samples = readFloat32LE(file)
            val decoded = decodeRawWaveform(cfg, samples)
            val first = decoded.firstOrNull()
            appendLog(
                "raw_decode_file path=$wavePath codec=${resolvedRawCodec(cfg)} samples=${samples.size} " +
                    "decodedFrames=${decoded.size} firstText=${first?.let { previewText(it) } ?: ""} " +
                    "firstHex=${first?.let { hexPrefix(it) } ?: ""}",
            )
        }
    }

    private fun encodeRawWaveform(cfg: SessionConfig, payload: ByteArray): FloatArray {
        return when (resolvedRawCodec(cfg)) {
            RawCodec.OOK -> OOKToneCodec(cfg).encode(payload)
            RawCodec.MORSE -> MorseToneCodec(cfg).encode(payload)
            RawCodec.REVERSE_BURST -> ReverseBurstCodec(cfg).encode(payload)
            RawCodec.DTMF -> DTMFCodec(cfg).encode(payload)
            RawCodec.NIBBLE -> NibbleToneCodec(cfg).encode(payload)
            RawCodec.BASIC, RawCodec.AUTO -> BasicToneCodec(cfg).encode(payload)
        }
    }

    private fun decodeRawWaveform(cfg: SessionConfig, samples: FloatArray): List<ByteArray> {
        return when (resolvedRawCodec(cfg)) {
            RawCodec.OOK -> OOKToneCodec(cfg).ingest(samples)
            RawCodec.MORSE -> MorseToneCodec(cfg).ingest(samples)
            RawCodec.REVERSE_BURST -> ReverseBurstCodec(cfg).ingest(samples)
            RawCodec.DTMF -> DTMFCodec(cfg).ingest(samples)
            RawCodec.NIBBLE -> NibbleToneCodec(cfg).ingest(samples)
            RawCodec.BASIC, RawCodec.AUTO -> BasicToneCodec(cfg).ingest(samples)
        }
    }

    private fun resolvedRawCodec(cfg: SessionConfig): RawCodec {
        if (cfg.rawCodec != RawCodec.AUTO) {
            return cfg.rawCodec
        }
        if (cfg.bandStartHz == cfg.bandEndHz) {
            return RawCodec.OOK
        }
        return if (cfg.role == Role.MASTER) RawCodec.REVERSE_BURST else RawCodec.BASIC
    }

    private fun writeFloat32LE(path: String, samples: FloatArray) {
        val out = ByteBuffer.allocate(samples.size * java.lang.Float.BYTES)
            .order(ByteOrder.LITTLE_ENDIAN)
        for (sample in samples) {
            out.putFloat(sample)
        }
        File(path).writeBytes(out.array())
    }

    private fun readFloat32LE(file: File): FloatArray {
        val bytes = file.readBytes()
        if (bytes.size < 4 || (bytes.size % 4) != 0) {
            throw IllegalArgumentException("invalid float32le byteCount=${bytes.size}")
        }
        val samples = FloatArray(bytes.size / java.lang.Float.BYTES)
        val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        for (i in samples.indices) {
            samples[i] = buf.float
        }
        return samples
    }
}
