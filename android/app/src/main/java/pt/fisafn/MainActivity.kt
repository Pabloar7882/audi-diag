package pt.fisafn

import android.app.Activity
import android.app.AlertDialog
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.Spinner
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private lateinit var usbManager: UsbManager
    private lateinit var diagnosticClient: AndroidDiagnosticClient
    private lateinit var scanner: UsbDeviceScanner
    private lateinit var usbDeviceSpinner: Spinner
    private lateinit var baudSpinner: Spinner
    private lateinit var groupSpinner: Spinner
    private lateinit var readGroupButton: Button
    private lateinit var monitorIntervalSpinner: Spinner
    private lateinit var monitorStartButton: Button
    private lateinit var monitorStopButton: Button
    private lateinit var openAdapterButton: Button
    private lateinit var closeAdapterButton: Button
    private lateinit var exportJsonButton: Button
    private lateinit var exportCsvButton: Button
    private lateinit var catalogStatusView: TextView
    private lateinit var usbStatus: TextView
    private lateinit var statusView: TextView
    private lateinit var ecuView: TextView
    private lateinit var valuesView: TextView
    private lateinit var sourceNote: TextView
    private lateinit var localEchoSwitch: Switch
    private val permissionFlow = UsbPermissionFlow()
    private val sessionCallbacks = DiagnosticSessionCallbacks()
    private val diagnosticTrace = AndroidDiagnosticTrace()
    private val ioExecutor = Executors.newSingleThreadExecutor()
    private val keepAliveHandler = Handler(Looper.getMainLooper())
    private var keepAliveRunnable: Runnable? = null
    private var candidates: List<UsbCandidate> = emptyList()
    private var receiverRegistered = false
    @Volatile private var activityResumed = false
    @Volatile private var monitorActive = false
    @Volatile private var monitorGeneration = 0L
    @Volatile private var monitorRequestInFlight = false
    private var monitorScheduler: SequentialGroupMonitor? = null
    private var monitorRunnable: Runnable? = null
    private var sensorCatalog: SensorCatalog? = null
    @Volatile private var keepAliveGeneration = 0L
    @Volatile private var activeTraceSessionId: String? = null
    private var pendingTraceExport: PendingTraceExport? = null
    private val permissionAction = "pt.fisafn.USB_PERMISSION"

    private val usbReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                permissionAction -> handlePermissionResult(intent)
                UsbManager.ACTION_USB_DEVICE_ATTACHED -> refreshCandidates()
                UsbManager.ACTION_USB_DEVICE_DETACHED -> handleDetach(readDevice(intent))
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        usbManager = getSystemService(USB_SERVICE) as UsbManager
        scanner = UsbDeviceScanner(usbManager)
        diagnosticClient = AndroidDiagnosticClient(
            transportFactory = { deviceName, baudrate, localEcho ->
                val device = usbManager.deviceList[deviceName]
                    ?: throw Kw1281Exception("usb_disconnected", "O adaptador USB foi desligado")
                UsbKLineTransport(
                    manager = usbManager,
                    localEcho = localEcho,
                    operationAllowed = { activityResumed },
                ).also { it.open(device, baudrate) }
            },
            trace = diagnosticTrace,
            operationAllowed = { activityResumed },
        )

        statusView = findViewById(R.id.status)
        ecuView = findViewById(R.id.ecu)
        valuesView = findViewById(R.id.values)
        sourceNote = findViewById(R.id.sourceNote)
        catalogStatusView = findViewById(R.id.catalogStatus)
        usbStatus = findViewById(R.id.usb)
        usbDeviceSpinner = findViewById(R.id.usbDevice)
        baudSpinner = findViewById(R.id.baudRate)
        groupSpinner = findViewById(R.id.groupSelect)
        readGroupButton = findViewById(R.id.readGroup)
        monitorIntervalSpinner = findViewById(R.id.monitorInterval)
        monitorStartButton = findViewById(R.id.startMonitor)
        monitorStopButton = findViewById(R.id.stopMonitor)
        openAdapterButton = findViewById(R.id.usbOpenOnly)
        closeAdapterButton = findViewById(R.id.usbClose)
        exportJsonButton = findViewById(R.id.exportTraceJson)
        exportCsvButton = findViewById(R.id.exportTraceCsv)
        localEchoSwitch = findViewById(R.id.localEcho)

        loadSensorCatalog()
        configureSelectors()
        ContextCompat.registerReceiver(
            this,
            usbReceiver,
            IntentFilter().apply {
                addAction(permissionAction)
                addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
                addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
            },
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        receiverRegistered = true
        refreshCandidates()

        findViewById<Button>(R.id.replay).setOnClickListener {
            stopMonitoring(resumeKeepAlive = false, reason = "replay")
            cancelKeepAlive()
            sessionCallbacks.clear()
            diagnosticClient.close()
            readGroupButton.isEnabled = false
            updateMonitorControls()
            updateUsbControls()
            renderState(ReplayTransport().healthy())
        }
        findViewById<Button>(R.id.usbConnect).setOnClickListener { requestSelectedDevice() }
        openAdapterButton.setOnClickListener {
            requestSelectedDevice(UsbPermissionFlow.Action.OPEN_ADAPTER_ONLY)
        }
        closeAdapterButton.setOnClickListener { closeAdapter() }
        readGroupButton.setOnClickListener { readSelectedGroup() }
        monitorStartButton.setOnClickListener { startMonitoring() }
        monitorStopButton.setOnClickListener { stopMonitoring() }
        exportJsonButton.setOnClickListener { requestTraceExport(TraceFormat.JSON) }
        exportCsvButton.setOnClickListener { requestTraceExport(TraceFormat.CSV) }
    }

    private fun configureSelectors() {
        baudSpinner.adapter = spinnerAdapter(listOf("10400 baud", "9600 baud", "4800 baud"))
        val names = mapOf(1 to "Motor", 3 to "Massa de ar", 11 to "Turbo")
        val catalogGroups = sensorCatalog?.physicalGroups().orEmpty().associateBy { it.groupId }
        groupSpinner.adapter = spinnerAdapter(PhysicalGroupPolicy.allowedGroups.map { group ->
            val name = catalogGroups[group]?.name ?: names[group] ?: "Grupo ${group.toString().padStart(3, '0')}"
            "${group.toString().padStart(3, '0')} · $name"
        })
        monitorIntervalSpinner.adapter = spinnerAdapter(listOf("1 s por grupo", "2 s por grupo", "5 s por grupo"))
    }

    private fun loadSensorCatalog() {
        sensorCatalog = runCatching { SensorCatalog.fromAssets(assets) }
            .onFailure { catalogStatusView.text = "Catálogo local indisponível: ${it.message ?: "erro de leitura"}. A política física continua limitada aos grupos fixos 001, 003 e 011." }
            .getOrNull()
        val catalog = sensorCatalog ?: return
        val groups = PhysicalGroupPolicy.allowedGroups.joinToString(" · ") { id ->
            val group = catalog.group(id)
            "${id.toString().padStart(3, '0')} ${group?.name ?: "Grupo"}"
        }
        val fieldState = if (catalog.hasAfnSensorMapping) {
            "O catálogo contém candidatos AFN; as fórmulas continuam identificadas pelo estado de evidência."
        } else {
            "Sem mapeamento AFN exato de campos ou fórmulas confirmadas. Leituras físicas permanecem bytes brutos."
        }
        val metadataGroupCount = catalog.groups.count { it.status == CatalogStatus.METADATA_ONLY }
        val metadataCandidateCount = catalog.groups.sumOf { it.metadataCandidateCount }
        val futureState = "$metadataGroupCount grupos com referências em labels · $metadataCandidateCount campos candidatos agregados entre variantes; compatibilidade AFN não confirmada."
        catalogStatusView.text = "Catálogo partilhado v${catalog.schemaVersion} · consulta física autorizada: $groups.\n$futureState\n$fieldState"
    }

    private fun spinnerAdapter(items: List<String>): ArrayAdapter<String> =
        ArrayAdapter(this, android.R.layout.simple_spinner_item, items).also {
            it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        }

    private fun refreshCandidates() {
        candidates = runCatching { scanner.candidates() }.getOrElse { emptyList() }
        val labels = candidates.map { candidate ->
            "FTDI ${candidate.vendorId.toString(16).padStart(4, '0')}:${candidate.productId.toString(16).padStart(4, '0')} · ${candidate.deviceName}"
        }.ifEmpty { listOf("Nenhum adaptador FTDI detetado") }
        usbDeviceSpinner.adapter = spinnerAdapter(labels)
        usbStatus.text = if (candidates.isEmpty()) {
            "USB OTG: nenhum dispositivo reconhecido pelo driver FTDI."
        } else {
            "USB OTG: ${candidates.size} adaptador(es) detetado(s). Seleciona um e inicia a identificação manual."
        }
        updateUsbControls()
    }

    private fun requestSelectedDevice(action: UsbPermissionFlow.Action = UsbPermissionFlow.Action.IDENTIFY_ECU) {
        val candidate = candidates.getOrNull(usbDeviceSpinner.selectedItemPosition)
        if (candidate == null) {
            usbStatus.text = "USB OTG: liga um adaptador FTDI compatível e volta a tentar."
            return
        }
        val device = usbManager.deviceList[candidate.deviceName]
        if (device == null) {
            refreshCandidates()
            return
        }
        activeTraceSessionId = diagnosticTrace.beginSession(
            deviceName = candidate.deviceName,
            vendorId = candidate.vendorId,
            productId = candidate.productId,
            baudrate = selectedBaudrate(),
            localEcho = localEchoSwitch.isChecked,
        )
        val outcome = permissionFlow.begin(candidate.deviceName, usbManager.hasPermission(device), action)
        diagnosticTrace.record(
            direction = "state",
            phase = "usb_permission",
            result = outcome.name.lowercase(),
            sessionId = activeTraceSessionId,
        )
        when (outcome) {
            UsbPermissionFlow.Outcome.CONNECT -> connect(device)
            UsbPermissionFlow.Outcome.OPEN_ADAPTER -> openAdapterOnly(device)
            UsbPermissionFlow.Outcome.REQUEST_PERMISSION -> requestUsbPermission(device)
            else -> usbStatus.text = "USB OTG: estado de permissão inválido."
        }
    }

    private fun requestUsbPermission(device: UsbDevice) {
        val intent = Intent(permissionAction).setPackage(packageName)
        val permissionIntent = PendingIntent.getBroadcast(
            this,
            device.deviceId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
        )
        usbManager.requestPermission(device, permissionIntent)
        diagnosticTrace.record("state", "usb_permission", result = "request_sent", sessionId = activeTraceSessionId)
        usbStatus.text = "USB OTG: aguarda a autorização do utilizador."
    }

    private fun handlePermissionResult(intent: Intent) {
        val device = readDevice(intent) ?: return
        val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
        diagnosticTrace.record(
            direction = "state",
            phase = "usb_permission",
            result = if (granted) "granted" else "denied",
            sessionId = activeTraceSessionId,
        )
        when (permissionFlow.permissionResult(device.deviceName, granted)) {
            UsbPermissionFlow.Outcome.CONNECT -> connect(device)
            UsbPermissionFlow.Outcome.OPEN_ADAPTER -> openAdapterOnly(device)
            UsbPermissionFlow.Outcome.PERMISSION_DENIED ->
                usbStatus.text = "USB OTG: permissão recusada; não foi aberta qualquer porta."
            else -> Unit
        }
    }

    private fun connect(device: UsbDevice) {
        stopMonitoring(resumeKeepAlive = false, reason = "reconnect")
        cancelKeepAlive()
        val baudrate = selectedBaudrate()
        val localEcho = localEchoSwitch.isChecked
        val operation = sessionCallbacks.begin(device.deviceName, activeTraceSessionId)
        val clientGeneration = diagnosticClient.beginConnection()
        readGroupButton.isEnabled = false
        updateUsbControls()
        statusView.text = "USB · A IDENTIFICAR ECU"
        ecuView.text = "ECU: a aguardar resposta"
        valuesView.text = "A aguardar a resposta inicial KW1281…"
        sourceNote.text = "A ligação foi iniciada manualmente. Não são pedidos grupos automaticamente."
        ioExecutor.execute {
            if (!sessionCallbacks.isCurrent(operation)) return@execute
            try {
                val state = diagnosticClient.connect(device.deviceName, baudrate, localEcho, clientGeneration, operation.traceSessionId)
                runOnUiThread {
                    sessionCallbacks.runIfCurrent(operation) {
                        renderState(state)
                        readGroupButton.isEnabled = true
                        updateMonitorControls()
                        updateUsbControls()
                        usbStatus.text = "USB OTG: ECU identificada. As leituras exigem selecionar e pedir um grupo."
                        startKeepAlive(operation)
                    }
                }
            } catch (error: Exception) {
                runOnUiThread {
                    sessionCallbacks.runIfCurrent(operation) {
                        sessionCallbacks.clear()
                        diagnosticClient.close()
                        cancelKeepAlive()
                        readGroupButton.isEnabled = false
                        updateMonitorControls()
                        updateUsbControls()
                        statusView.text = "USB · ERRO DE LIGAÇÃO"
                        ecuView.text = "ECU: não identificada"
                        valuesView.text = error.message ?: "Falha ao comunicar com a ECU"
                        usbStatus.text = "USB OTG: ${error.message ?: "erro de comunicação"}"
                        sourceNote.text = "Sem resposta física validada. Confirma velocidade, eco local e adaptador."
                    }
                }
            }
        }
    }

    private fun openAdapterOnly(device: UsbDevice) {
        stopMonitoring(resumeKeepAlive = false, reason = "adapter-open")
        cancelKeepAlive()
        val baudrate = selectedBaudrate()
        val localEcho = localEchoSwitch.isChecked
        val operation = sessionCallbacks.begin(device.deviceName, activeTraceSessionId)
        val clientGeneration = diagnosticClient.beginConnection()
        readGroupButton.isEnabled = false
        updateMonitorControls()
        updateUsbControls()
        statusView.text = "USB · A ABRIR ADAPTADOR"
        ecuView.text = "ECU: não identificada"
        valuesView.text = "A verificar apenas a abertura da porta USB serial…"
        sourceNote.text = "Este passo não envia BREAK, slow-init nem bytes KW1281."
        ioExecutor.execute {
            if (!sessionCallbacks.isCurrent(operation)) return@execute
            try {
                val state = diagnosticClient.openAdapter(device.deviceName, baudrate, localEcho, clientGeneration, operation.traceSessionId)
                runOnUiThread {
                    sessionCallbacks.runIfCurrent(operation) {
                        renderState(state)
                        readGroupButton.isEnabled = false
                        updateMonitorControls()
                        updateUsbControls()
                        usbStatus.text = "USB OTG: porta FTDI aberta; nenhum pedido KW1281 foi enviado."
                    }
                }
            } catch (error: Exception) {
                runOnUiThread {
                    sessionCallbacks.runIfCurrent(operation) {
                        sessionCallbacks.clear()
                        diagnosticClient.close()
                        readGroupButton.isEnabled = false
                        updateMonitorControls()
                        updateUsbControls()
                        statusView.text = "USB · FALHA AO ABRIR ADAPTADOR"
                        ecuView.text = "ECU: não identificada"
                        valuesView.text = error.message ?: "Falha ao abrir a porta USB serial"
                        usbStatus.text = "USB OTG: ${error.message ?: "erro ao abrir adaptador"}"
                        sourceNote.text = "A identificação ECU não foi iniciada."
                    }
                }
            }
        }
    }

    private fun closeAdapter() {
        val traceSessionId = currentTraceSessionId()
        stopMonitoring(resumeKeepAlive = false, reason = "adapter-close")
        cancelKeepAlive()
        sessionCallbacks.clear()
        diagnosticTrace.record("state", "adapter_close", result = "requested", sessionId = traceSessionId)
        diagnosticClient.close()
        readGroupButton.isEnabled = false
        statusView.text = "USB · ADAPTADOR FECHADO"
        ecuView.text = "ECU: não identificada"
        valuesView.text = "Sem dados atuais"
        sourceNote.text = "Porta USB serial fechada manualmente."
        usbStatus.text = "USB OTG: adaptador fechado."
        updateMonitorControls()
        updateUsbControls()
    }

    private fun readSelectedGroup() {
        val operation = sessionCallbacks.current() ?: return
        cancelKeepAlive()
        val groups = PhysicalGroupPolicy.allowedGroups
        val group = groups.getOrElse(groupSpinner.selectedItemPosition) { 1 }
        diagnosticTrace.record("state", "group_read_requested", result = "group=$group", sessionId = operation.traceSessionId)
        readGroupButton.isEnabled = false
        updateMonitorControls()
        statusView.text = "LIVE · A LER GRUPO ${group.toString().padStart(3, '0')}"
        ioExecutor.execute {
            if (!sessionCallbacks.isCurrent(operation)) return@execute
            try {
                val state = diagnosticClient.readGroup(group)
                runOnUiThread {
                    sessionCallbacks.runIfCurrent(operation) {
                        renderState(state)
                        readGroupButton.isEnabled = true
                        updateMonitorControls()
                        startKeepAlive(operation)
                    }
                }
            } catch (error: Exception) {
                runOnUiThread {
                    sessionCallbacks.runIfCurrent(operation) {
                        val connected = diagnosticClient.isConnected()
                        if (!connected) sessionCallbacks.clear()
                        readGroupButton.isEnabled = connected
                        updateMonitorControls()
                        statusView.text = "LIVE · ERRO DE LEITURA"
                        valuesView.text = error.message ?: "Falha ao ler o grupo"
                        sourceNote.text = "O grupo não foi considerado válido; nenhuma escrita ou procura automática foi realizada."
                        if (connected) startKeepAlive(operation) else cancelKeepAlive()
                    }
                }
            }
        }
    }

    private fun startMonitoring() {
        val operation = sessionCallbacks.current()
        if (operation == null || !diagnosticClient.isConnected()) {
            usbStatus.text = "USB OTG: identifica primeiro a ECU para iniciar a monitorização."
            updateMonitorControls()
            return
        }
        if (!readGroupButton.isEnabled || monitorRequestInFlight || monitorActive) return
        val periodMillis = listOf(1_000L, 2_000L, 5_000L)
            .getOrElse(monitorIntervalSpinner.selectedItemPosition) { 1_000L }
        val scheduler = SequentialGroupMonitor(periodMillis)
        scheduler.start()
        monitorScheduler = scheduler
        val generation = ++monitorGeneration
        monitorActive = true
        cancelKeepAlive()
        diagnosticTrace.record("state", "monitor_start", result = "period_ms=$periodMillis; groups=001,003,011", sessionId = operation.traceSessionId)
        readGroupButton.isEnabled = false
        updateMonitorControls()
        statusView.text = "LIVE · MONITORIZAÇÃO SEQUENCIAL"
        sourceNote.text = "Monitorização iniciada manualmente · grupos 001 → 003 → 011 · respostas em bytes brutos."
        scheduleMonitorRead(operation, generation, 0L)
    }

    private fun scheduleMonitorRead(
        operation: DiagnosticSessionCallbacks.Token,
        generation: Long,
        delayMillis: Long,
    ) {
        monitorRunnable?.let(keepAliveHandler::removeCallbacks)
        val task = Runnable {
            if (!monitorActive || generation != monitorGeneration || !sessionCallbacks.isCurrent(operation)) return@Runnable
            val scheduler = monitorScheduler ?: return@Runnable
            val request = scheduler.beginRequest() ?: return@Runnable
            monitorRequestInFlight = true
            cancelKeepAlive()
            updateMonitorControls()
            statusView.text = "LIVE · A LER GRUPO ${request.groupId.toString().padStart(3, '0')}"
            ioExecutor.execute {
                if (!monitorActive || generation != monitorGeneration || !sessionCallbacks.isCurrent(operation)) {
                    scheduler.failRequest(request)
                    runOnUiThread {
                        monitorRequestInFlight = false
                        updateMonitorControls()
                    }
                    return@execute
                }
                try {
                    val state = diagnosticClient.readGroup(request.groupId)
                    val advance = scheduler.completeRequest(request)
                    runOnUiThread {
                        monitorRequestInFlight = false
                        if (!sessionCallbacks.isCurrent(operation)) {
                            updateMonitorControls()
                            return@runOnUiThread
                        }
                        renderState(state)
                        if (monitorActive && generation == monitorGeneration && advance != null) {
                            statusView.text = "LIVE · MONITORIZAÇÃO · PRÓXIMO ${advance.nextGroupId.toString().padStart(3, '0')}"
                            scheduleMonitorRead(operation, generation, advance.delayMillis)
                        } else {
                            if (!monitorActive) {
                                statusView.text = "LIVE · MONITORIZAÇÃO PARADA · SEM PEDIDO SEGUINTE"
                                sourceNote.text = "A resposta em curso foi concluída; a monitorização está parada e não foi pedido outro grupo."
                            }
                            readGroupButton.isEnabled = diagnosticClient.isConnected()
                            monitorStartButton.isEnabled = diagnosticClient.isConnected() && activityResumed
                            monitorStopButton.isEnabled = false
                            if (activityResumed && diagnosticClient.isConnected()) startKeepAlive(operation, allowWhileMonitoring = true)
                        }
                        if (monitorActive && activityResumed && diagnosticClient.isConnected()) {
                            startKeepAlive(operation, allowWhileMonitoring = true)
                        }
                        updateMonitorControls()
                    }
                } catch (error: Exception) {
                    scheduler.failRequest(request)
                    runOnUiThread {
                        monitorRequestInFlight = false
                        if (sessionCallbacks.isCurrent(operation) && generation == monitorGeneration) {
                            monitorActive = false
                            monitorGeneration++
                            monitorRunnable?.let(keepAliveHandler::removeCallbacks)
                            monitorScheduler?.stop()
                            statusView.text = "LIVE · MONITORIZAÇÃO SUSPENSA"
                            valuesView.text = error.message ?: "Falha ao ler o grupo monitorizado"
                            sourceNote.text = "A monitorização foi suspensa após erro. Nenhum grupo seguinte foi pedido."
                            readGroupButton.isEnabled = diagnosticClient.isConnected()
                            if (activityResumed && diagnosticClient.isConnected()) startKeepAlive(operation, allowWhileMonitoring = true)
                        }
                        updateMonitorControls()
                    }
                }
            }
        }
        monitorRunnable = task
        keepAliveHandler.postDelayed(task, delayMillis)
    }

    private fun stopMonitoring(resumeKeepAlive: Boolean = true, reason: String = "manual") {
        val wasActive = monitorActive
        if (wasActive) diagnosticTrace.record("state", "monitor_stop", result = reason, sessionId = currentTraceSessionId())
        monitorActive = false
        monitorGeneration++
        monitorScheduler?.stop()
        monitorRunnable?.let(keepAliveHandler::removeCallbacks)
        monitorRunnable = null
        if (::readGroupButton.isInitialized) {
            readGroupButton.isEnabled = diagnosticClient.isConnected() && !monitorRequestInFlight
        }
        if (::monitorStopButton.isInitialized) monitorStopButton.isEnabled = false
        if (wasActive && ::statusView.isInitialized) {
            statusView.text = when {
                monitorRequestInFlight -> "LIVE · A AGUARDAR RESPOSTA ATUAL · SEM PRÓXIMO GRUPO"
                reason == "pause" -> "LIVE · MONITORIZAÇÃO PAUSADA"
                else -> "LIVE · MONITORIZAÇÃO PARADA"
            }
        }
        if (::sourceNote.isInitialized && wasActive) {
            sourceNote.text = when (reason) {
                "pause" -> "A monitorização parou quando a app saiu de primeiro plano; ativa-a novamente de forma manual."
                "replay" -> "Sessão física fechada antes de mostrar a reprodução sintética."
                "reconnect" -> "Monitorização parada para iniciar uma nova identificação da ECU."
                else -> "Monitorização parada manualmente. A leitura pontual continua disponível separadamente."
            }
        }
        updateMonitorControls()
        val operation = sessionCallbacks.current()
        if (resumeKeepAlive && activityResumed && operation != null && diagnosticClient.isConnected()) {
            startKeepAlive(operation)
        }
    }

    private fun updateMonitorControls() {
        if (!::monitorStartButton.isInitialized) return
        val connected = diagnosticClient.isConnected() && sessionCallbacks.current() != null
        monitorStartButton.isEnabled = connected && activityResumed && !monitorActive && !monitorRequestInFlight && readGroupButton.isEnabled
        monitorStopButton.isEnabled = monitorActive
    }

    private fun updateUsbControls() {
        if (!::openAdapterButton.isInitialized || !::closeAdapterButton.isInitialized) return
        openAdapterButton.isEnabled =
            candidates.isNotEmpty() && !diagnosticClient.isAdapterOpen() && sessionCallbacks.current() == null
        closeAdapterButton.isEnabled = diagnosticClient.isAdapterOpen()
    }

    private fun selectedBaudrate(): Int = listOf(10400, 9600, 4800)
        .getOrElse(baudSpinner.selectedItemPosition) { 10400 }

    private fun requestTraceExport(format: TraceFormat) {
        if (pendingTraceExport != null) return
        val snapshot = diagnosticTrace.snapshot()
        val content = runCatching {
            when (format) {
                TraceFormat.JSON -> diagnosticTrace.exportJson(snapshot)
                TraceFormat.CSV -> diagnosticTrace.exportCsv(snapshot)
            }
        }.getOrElse { error ->
            Toast.makeText(this, "Trace demasiado grande para exportar: ${error.message}", Toast.LENGTH_LONG).show()
            return
        }
        val displayName = "fis-afn-trace-${System.currentTimeMillis()}.${format.extension}"
        AlertDialog.Builder(this)
            .setTitle("Exportar trace de diagnóstico")
            .setMessage(
                "O trace contém bytes brutos RX/TX. A identificação inicial da ECU pode incluir dados pessoais do veículo, incluindo VIN. Guarda o ficheiro apenas num local privado."
            )
            .setNegativeButton("Cancelar", null)
            .setPositiveButton("Escolher local") { _, _ -> launchTraceDocumentPicker(format, displayName, content) }
            .show()
    }

    private fun launchTraceDocumentPicker(format: TraceFormat, displayName: String, content: String) {
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = format.mimeType
            putExtra(Intent.EXTRA_TITLE, displayName)
        }
        pendingTraceExport = PendingTraceExport(format, content)
        exportJsonButton.isEnabled = false
        exportCsvButton.isEnabled = false
        diagnosticTrace.record("state", "trace_export", result = "document_picker_opened; format=${format.extension}")
        runCatching { startActivityForResult(intent, TRACE_EXPORT_REQUEST) }
            .onFailure { error ->
                pendingTraceExport = null
                updateTraceExportControls()
                diagnosticTrace.record("error", "trace_export_picker", error = error.javaClass.simpleName, result = error.message)
                Toast.makeText(this, "Não foi possível abrir o seletor de ficheiros.", Toast.LENGTH_LONG).show()
            }
    }

    @Deprecated("Uses the platform document picker result callback")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != TRACE_EXPORT_REQUEST) return
        val export = pendingTraceExport
        pendingTraceExport = null
        updateTraceExportControls()
        val uri = if (resultCode == RESULT_OK) data?.data else null
        if (export == null || uri == null) {
            diagnosticTrace.record("state", "trace_export", result = "cancelled")
            return
        }
        ioExecutor.execute {
            val result = runCatching {
                val bytes = export.content.toByteArray(Charsets.UTF_8)
                contentResolver.openOutputStream(uri, "wt")?.use { stream -> stream.write(bytes) }
                    ?: throw IllegalStateException("O fornecedor de documentos não abriu o ficheiro")
                bytes.size
            }
            result.onSuccess { byteCount ->
                diagnosticTrace.record("state", "trace_export", result = "saved; format=${export.format.extension}; bytes=$byteCount")
                runOnUiThread {
                    Toast.makeText(this, "Trace guardado (${byteCount} bytes).", Toast.LENGTH_LONG).show()
                }
            }.onFailure { error ->
                diagnosticTrace.record(
                    direction = "error",
                    phase = "trace_export_write",
                    error = error.javaClass.simpleName,
                    result = error.message,
                )
                runOnUiThread {
                    Toast.makeText(this, "Falha ao guardar o trace: ${error.message ?: "erro de escrita"}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun updateTraceExportControls() {
        if (!::exportJsonButton.isInitialized || !::exportCsvButton.isInitialized) return
        val enabled = pendingTraceExport == null
        exportJsonButton.isEnabled = enabled
        exportCsvButton.isEnabled = enabled
    }

    private fun currentTraceSessionId(): String? =
        sessionCallbacks.current()?.traceSessionId ?: activeTraceSessionId

    private fun startKeepAlive(
        operation: DiagnosticSessionCallbacks.Token? = sessionCallbacks.current(),
        allowWhileMonitoring: Boolean = false,
    ) {
        val active = operation ?: return
        if (!sessionCallbacks.isCurrent(active) || (monitorActive && !allowWhileMonitoring)) return
        cancelKeepAlive()
        if (!activityResumed || !diagnosticClient.isConnected()) return
        val task = object : Runnable {
            override fun run() {
                val generation = keepAliveGeneration
                if (!activityResumed || !sessionCallbacks.isCurrent(active) || (monitorActive && !allowWhileMonitoring)) return
                ioExecutor.execute {
                    if (generation != keepAliveGeneration || !activityResumed || !sessionCallbacks.isCurrent(active) || monitorRequestInFlight) return@execute
                    try {
                        diagnosticClient.keepAlive()
                        runOnUiThread {
                            sessionCallbacks.runIfCurrent(active) { startKeepAlive(active, allowWhileMonitoring) }
                        }
                    } catch (error: Exception) {
                        runOnUiThread { failPhysicalSession(active, error) }
                    }
                }
            }
        }
        keepAliveRunnable = task
        keepAliveHandler.postDelayed(task, KEEPALIVE_INTERVAL_MS)
    }

    private fun cancelKeepAlive() {
        keepAliveGeneration++
        keepAliveRunnable?.let(keepAliveHandler::removeCallbacks)
        keepAliveRunnable = null
    }

    private fun failPhysicalSession(operation: DiagnosticSessionCallbacks.Token, error: Exception) {
        sessionCallbacks.runIfCurrent(operation) {
            stopMonitoring(resumeKeepAlive = false, reason = "error")
            sessionCallbacks.clear()
            cancelKeepAlive()
            diagnosticClient.close()
            readGroupButton.isEnabled = false
            updateMonitorControls()
        updateUsbControls()
            statusView.text = "USB · SESSÃO TERMINADA"
            ecuView.text = "ECU: ligação terminada"
            valuesView.text = error.message ?: "A sessão KW1281 deixou de responder."
            sourceNote.text = "A sessão foi fechada. Volta a identificar a ECU antes de pedir outro grupo."
            usbStatus.text = "USB OTG: sessão terminada; inicia uma nova identificação manual."
        }
    }

    private fun handleDetach(device: UsbDevice?) {
        val name = device?.deviceName ?: return
        val operationTraceSessionId = sessionCallbacks.current()
            ?.takeIf { it.deviceName == name }
            ?.traceSessionId
        diagnosticTrace.record(
            "state", "usb_detach", result = "device=$name",
            sessionId = operationTraceSessionId ?: activeTraceSessionId,
        )
        permissionFlow.detached(name)
        if (sessionCallbacks.current()?.deviceName != name) {
            refreshCandidates()
            return
        }
        stopMonitoring(resumeKeepAlive = false, reason = "detach")
        cancelKeepAlive()
        sessionCallbacks.clear()
        readGroupButton.isEnabled = false
        diagnosticClient.disconnect()
        updateMonitorControls()
        updateUsbControls()
        statusView.text = "USB · DESLIGADO"
        ecuView.text = "ECU: ligação terminada"
        valuesView.text = "Sem dados atuais"
        sourceNote.text = "O adaptador foi desligado; os valores atuais foram limpos."
        usbStatus.text = "USB OTG: adaptador desligado."
        refreshCandidates()
    }

    private fun renderState(state: DiagnosticState) {
        statusView.text = "${state.source.uppercase()} · ${state.status.uppercase()} · ${state.freshness.uppercase()}"
        val identityDetails = listOfNotNull(state.ecuComponent, state.ecuSoftwareVersion)
            .joinToString(" · ")
        val extra = state.ecuExtraIdentification?.takeIf { it.isNotBlank() }?.let { "\nExtra: $it" }.orEmpty()
        ecuView.text = "ECU: ${state.ecu ?: "não identificada"}" +
            (if (identityDetails.isNotBlank()) "\n$identityDetails" else "") + extra
        valuesView.text = state.values.joinToString("\n") { value ->
            val shown = value.value?.toString() ?: value.raw ?: "—"
            "${value.key}: $shown ${value.unit} · ${value.source}"
        }.ifEmpty { if (state.status == "identified") "ECU identificada. Escolhe um grupo para ler." else "A aguardar amostras…" }
        sourceNote.text = if (state.status == "adapter_open") {
            "ADAPTADOR ABERTO · SEM IDENTIFICAÇÃO ECU · SEM TRÁFEGO KW1281"
        } else if (state.source == "replay") {
            "DADOS SINTÉTICOS · REPLAY"
        } else if (state.freshness == "fresh") {
            "LEITURA LIVE · BYTES BRUTOS KW1281 · DESCODIFICAÇÃO AINDA NÃO VALIDADA"
        } else {
            "Ligação física aberta manualmente; ainda não existe amostra de medição."
        }
    }

    @Suppress("DEPRECATION")
    private fun readDevice(intent: Intent): UsbDevice? =
        if (android.os.Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
        } else {
            intent.getParcelableExtra(UsbManager.EXTRA_DEVICE)
        }

    override fun onDestroy() {
        sessionCallbacks.clear()
        stopMonitoring(resumeKeepAlive = false, reason = "close")
        cancelKeepAlive()
        diagnosticClient.close()
        ioExecutor.shutdownNow()
        if (receiverRegistered) unregisterReceiver(usbReceiver)
        super.onDestroy()
    }

    override fun onPause() {
        activityResumed = false
        diagnosticTrace.record("state", "activity_pause", result = "foreground IO timers cancelled", sessionId = currentTraceSessionId())
        stopMonitoring(resumeKeepAlive = false, reason = "pause")
        cancelKeepAlive()
        super.onPause()
    }

    override fun onResume() {
        super.onResume()
        activityResumed = true
        if (::diagnosticClient.isInitialized) {
            diagnosticTrace.record("state", "activity_resume", result = "foreground", sessionId = currentTraceSessionId())
            if (sessionCallbacks.current() != null && !diagnosticClient.isConnected() && !diagnosticClient.isAdapterOpen()) {
                sessionCallbacks.clear()
                readGroupButton.isEnabled = false
                statusView.text = "USB · SESSÃO CANCELADA EM SEGUNDO PLANO"
                valuesView.text = "Volta a identificar manualmente a ECU."
                usbStatus.text = "USB OTG: a sessão foi cancelada ao sair de primeiro plano."
            }
            updateMonitorControls()
            updateUsbControls()
            startKeepAlive()
        }
    }

    companion object {
        private const val KEEPALIVE_INTERVAL_MS = 250L
        private const val TRACE_EXPORT_REQUEST = 8120
    }

    private enum class TraceFormat(val extension: String, val mimeType: String) {
        JSON("json", "application/json"),
        CSV("csv", "text/csv"),
    }

    private data class PendingTraceExport(
        val format: TraceFormat,
        val content: String,
    )
}
