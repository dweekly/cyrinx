package com.dweekly.cyrinxhil

import android.Manifest
import android.content.Context
import android.media.AudioManager
import android.os.Vibrator
import android.os.VibrationEffect
import android.os.Build
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
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

        // Programmatic acoustic gain staging calibration to safe linear region (72% sweet-spot)
        val audioManager = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val maxVolume = audioManager.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val targetIndex = (maxVolume * 0.72f).toInt()
        audioManager.setStreamVolume(AudioManager.STREAM_MUSIC, targetIndex, 0)
        appendLog("[AcousticCalibration] Android Stream volume auto-calibrated to: $targetIndex / $maxVolume")

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
            else -> appendLog("unknown automation cmd=$cmd")
        }
    }

    private fun runAutomationScenario(role: Role, intent: android.content.Intent) {
        val durationSec = intent.getIntExtra("duration_sec", 15).coerceIn(5, 180)
        val intervalMs = intent.getIntExtra("send_interval_ms", 750).coerceIn(250, 5_000)

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
                val bePayload = "probe:${System.currentTimeMillis()}".toByteArray(Charsets.UTF_8)
                val beRc = sendProbeInternal(current, bestEffort = true, payload = bePayload)
                appendLog("scenario send BE rc=$beRc")
                if (counter % 4 == 0) {
                    val relPayload = "probe-reliable:${System.currentTimeMillis()}".toByteArray(Charsets.UTF_8)
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
            channels = overrideChannels ?: 1,
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
