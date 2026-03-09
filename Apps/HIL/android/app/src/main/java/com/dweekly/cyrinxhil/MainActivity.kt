package com.dweekly.cyrinxhil

import android.Manifest
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

        handleAutomationIntent(intent)
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

        stopSession()

        val config = SessionConfig(
            role = role,
            sampleRateHz = overrideSampleRateHz ?: 48_000,
            bandStartHz = overrideBandStartHz ?: 18_500,
            bandEndHz = overrideBandEndHz ?: 21_000,
            txGainCap = overrideTxGainCap ?: 0.70f,
            dcssSymbolSamples = overrideDcssSymbolSamples ?: 256,
            preambleSyncThreshold = overrideSyncThreshold ?: 0.25f,
        )

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
        statusText.text = "Status: Stopped"
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

        appendLog(
            "automation cmd=$cmd role=${role?.name ?: "unchanged"} sampleRate=${overrideSampleRateHz ?: 48_000} " +
                "band=${overrideBandStartHz ?: 18_500}...${overrideBandEndHz ?: 21_000} " +
                "gain=${overrideTxGainCap ?: 0.70f} dcss=${overrideDcssSymbolSamples ?: 256} " +
                "sync=${overrideSyncThreshold ?: 0.25f}",
        )
        when (cmd) {
            "start" -> runOnUiThread { startSession(role ?: selectedRole()) }
            "stop" -> runOnUiThread { stopSession() }
            "send_be" -> sendProbe(bestEffort = true)
            "send_rel" -> sendProbe(bestEffort = false)
            "receive" -> receiveOnce()
            "refresh" -> refreshDiagnostics()
            "beacon" -> playBeacon()
            "scenario" -> runAutomationScenario(role ?: selectedRole(), intent)
            "self_test" -> runPhySelfTest(role ?: selectedRole())
            "decode_file" -> runDecodeFile(role ?: selectedRole(), intent)
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

    private fun runPhySelfTest(role: Role) {
        ioExecutor.execute {
            val cfg = SessionConfig(
                role = role,
                sampleRateHz = overrideSampleRateHz ?: 48_000,
                bandStartHz = overrideBandStartHz ?: 18_500,
                bandEndHz = overrideBandEndHz ?: 21_000,
                txGainCap = overrideTxGainCap ?: 0.70f,
                dcssSymbolSamples = overrideDcssSymbolSamples ?: 256,
                preambleSyncThreshold = overrideSyncThreshold ?: 0.25f,
            )
            val phy = AcousticPhyLink(cfg)
            val frame = ByteArray(48) { idx -> ((idx * 13) and 0xFF).toByte() }
            val encoded = phy.encode(frame)
            val decoded = phy.ingest(encoded)
            val ok = decoded.firstOrNull()?.frame?.contentEquals(frame) == true
            appendLog(
                "self_test ok=$ok encodedSamples=${encoded.size} decodedFrames=${decoded.size} " +
                    "sampleRate=${cfg.sampleRateHz} band=${cfg.bandStartHz}...${cfg.bandEndHz} dcss=${cfg.dcssSymbolSamples} " +
                    "sync=${cfg.preambleSyncThreshold}",
            )
        }
    }

    private fun runDecodeFile(role: Role, intent: android.content.Intent) {
        ioExecutor.execute {
            val cfg = SessionConfig(
                role = role,
                sampleRateHz = overrideSampleRateHz ?: 48_000,
                bandStartHz = overrideBandStartHz ?: 18_500,
                bandEndHz = overrideBandEndHz ?: 21_000,
                txGainCap = overrideTxGainCap ?: 0.70f,
                dcssSymbolSamples = overrideDcssSymbolSamples ?: 256,
                preambleSyncThreshold = overrideSyncThreshold ?: 0.25f,
            )
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
}
