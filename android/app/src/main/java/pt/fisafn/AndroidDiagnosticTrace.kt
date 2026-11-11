package pt.fisafn

import org.json.JSONArray
import org.json.JSONObject
import java.nio.charset.StandardCharsets
import java.util.ArrayDeque
import java.util.LinkedHashMap

internal data class TraceTimestamp(val wallSeconds: Double, val monotonicSeconds: Double)

internal data class AndroidTraceSession(
    val id: String,
    val deviceName: String,
    val vendorId: Int,
    val productId: Int,
    val baudrate: Int,
    val dataBits: Int = 8,
    val parity: String = "none",
    val stopBits: Int = 1,
    val localEcho: Boolean,
    val ecuPartNumber: String? = null,
    val ecuComponent: String? = null,
    val ecuSoftwareVersion: String? = null,
    val extraIdentificationPresent: Boolean = false,
)

internal data class AndroidTraceEvent(
    val sequence: Long,
    val sessionId: String?,
    val timestamp: Double,
    val monotonic: Double,
    val direction: String,
    val phase: String,
    val dataHex: String,
    val result: String?,
    val error: String?,
    val interpretation: String?,
    val origin: String,
    val durationMs: Double?,
    val truncatedBytes: Int,
)

internal data class AndroidDiagnosticTraceSnapshot(
    val sessions: List<AndroidTraceSession>,
    val events: List<AndroidTraceEvent>,
)

internal class AndroidDiagnosticTrace(
    private val clock: () -> TraceTimestamp = {
        TraceTimestamp(System.currentTimeMillis() / 1000.0, System.nanoTime() / 1_000_000_000.0)
    },
    private val maxEvents: Int = DEFAULT_MAX_EVENTS,
    private val maxEventBytes: Int = DEFAULT_MAX_EVENT_BYTES,
    private val maxExportBytes: Int = DEFAULT_MAX_EXPORT_BYTES,
) {
    private val lock = Any()
    private val events = ArrayDeque<AndroidTraceEvent>()
    private val sessions = LinkedHashMap<String, AndroidTraceSession>()
    private var activeSessionId: String? = null
    private var nextEventSequence = 0L
    private var nextSessionSequence = 0L

    init {
        require(maxEvents > 0) { "Trace event limit must be positive" }
        require(maxEventBytes > 0) { "Trace payload limit must be positive" }
        require(maxExportBytes > 0) { "Trace export limit must be positive" }
    }

    fun beginSession(
        deviceName: String,
        vendorId: Int,
        productId: Int,
        baudrate: Int,
        localEcho: Boolean,
    ): String = synchronized(lock) {
        val id = "s${++nextSessionSequence}"
        sessions[id] = AndroidTraceSession(
            id = id,
            deviceName = bounded(deviceName, MAX_DEVICE_NAME_LENGTH),
            vendorId = vendorId,
            productId = productId,
            baudrate = baudrate,
            localEcho = localEcho,
        )
        while (sessions.size > MAX_SESSIONS) sessions.remove(sessions.keys.first())
        activeSessionId = id
        recordLocked(
            direction = "state",
            phase = "session_start",
            result = "device=$deviceName; baudrate=$baudrate; local_echo=$localEcho",
            origin = "application_state",
            sessionId = id,
        )
        id
    }

    fun currentSessionId(): String? = synchronized(lock) { activeSessionId }

    fun record(
        direction: String,
        phase: String,
        data: ByteArray = byteArrayOf(),
        result: String? = null,
        error: String? = null,
        interpretation: String? = null,
        origin: String = defaultOrigin(direction),
        sessionId: String? = null,
        durationMs: Double? = null,
    ) = synchronized(lock) {
        recordLocked(
            direction = direction,
            phase = phase,
            data = data,
            result = result,
            error = error,
            interpretation = interpretation,
            origin = origin,
            sessionId = sessionId ?: activeSessionId,
            durationMs = durationMs,
        )
    }

    fun updateIdentity(
        sessionId: String?,
        partNumber: String,
        component: String?,
        softwareVersion: String?,
        extraIdentificationPresent: Boolean,
    ) = synchronized(lock) {
        val id = sessionId ?: activeSessionId
        if (id != null) {
            val current = sessions[id]
            if (current != null) {
                sessions[id] = current.copy(
                    ecuPartNumber = bounded(partNumber, MAX_IDENTITY_LENGTH),
                    ecuComponent = component?.let { bounded(it, MAX_IDENTITY_LENGTH) },
                    ecuSoftwareVersion = softwareVersion?.let { bounded(it, MAX_IDENTITY_LENGTH) },
                    extraIdentificationPresent = extraIdentificationPresent,
                )
            }
        }
        val summary = buildString {
            append("part_number=").append(bounded(partNumber, MAX_IDENTITY_LENGTH))
            component?.let { append("; component=").append(bounded(it, MAX_IDENTITY_LENGTH)) }
            softwareVersion?.let { append("; software_version=").append(bounded(it, MAX_IDENTITY_LENGTH)) }
            append("; extra_identification_present=").append(extraIdentificationPresent)
        }
        recordLocked(
            direction = "identity",
            phase = "identity_decode",
            result = summary,
            origin = "protocol_decode",
            sessionId = id,
        )
    }

    fun snapshot(): AndroidDiagnosticTraceSnapshot = synchronized(lock) {
        AndroidDiagnosticTraceSnapshot(
            sessions = sessions.values.toList(),
            events = events.toList(),
        )
    }

    fun exportJson(snapshot: AndroidDiagnosticTraceSnapshot = snapshot()): String {
        val root = JSONObject()
            .put("schema", SCHEMA)
            .put("platform", "android")
            .put("evidence", JSONObject()
                .put("event_basis", "application API calls and bytes returned to the software")
                .put("electrical_waveform_measured", false)
                .put("break_request_proves_waveform", false))
            .put("sessions", JSONArray().apply { snapshot.sessions.forEach { put(it.toJson()) } })
            .put("events", JSONArray().apply { snapshot.events.forEach { put(it.toJson()) } })
        return root.toString().also { enforceExportLimit(it) }
    }

    fun exportCsv(snapshot: AndroidDiagnosticTraceSnapshot = snapshot()): String {
        val fields = CSV_FIELDS
        val out = StringBuilder().append(fields.joinToString(",")).append("\r\n")
        val sessionById = snapshot.sessions.associateBy { it.id }
        snapshot.events.forEach { event ->
            val session = event.sessionId?.let(sessionById::get)
            val values = listOf(
                event.timestamp.csv(), event.monotonic.csv(), event.direction, event.phase,
                event.dataHex, event.result.orEmpty(), event.error.orEmpty(), event.interpretation.orEmpty(),
                event.sequence.toString(), event.sessionId.orEmpty(), event.origin,
                event.durationMs?.csv().orEmpty(), event.truncatedBytes.toString(),
                session?.deviceName.orEmpty(), session?.vendorId?.toString().orEmpty(),
                session?.productId?.toString().orEmpty(), session?.baudrate?.toString().orEmpty(),
                session?.dataBits?.toString().orEmpty(), session?.parity.orEmpty(),
                session?.stopBits?.toString().orEmpty(), session?.localEcho?.toString().orEmpty(),
                session?.ecuPartNumber.orEmpty(), session?.ecuComponent.orEmpty(),
                session?.ecuSoftwareVersion.orEmpty(), session?.extraIdentificationPresent?.toString().orEmpty(),
                "false",
            )
            out.append(values.joinToString(",", transform = ::csvField)).append("\r\n")
        }
        return out.toString().also { enforceExportLimit(it) }
    }

    private fun recordLocked(
        direction: String,
        phase: String,
        data: ByteArray = byteArrayOf(),
        result: String? = null,
        error: String? = null,
        interpretation: String? = null,
        origin: String,
        sessionId: String?,
        durationMs: Double? = null,
    ) {
        val retained = minOf(data.size, maxEventBytes)
        val now = clock()
        events.addLast(AndroidTraceEvent(
            sequence = ++nextEventSequence,
            sessionId = sessionId,
            timestamp = now.wallSeconds,
            monotonic = now.monotonicSeconds,
            direction = bounded(direction, MAX_SHORT_TEXT_LENGTH),
            phase = bounded(phase, MAX_SHORT_TEXT_LENGTH),
            dataHex = data.take(retained).joinToString("") { BYTE_HEX[it.toInt() and 0xFF] },
            result = result?.let { bounded(it, MAX_LONG_TEXT_LENGTH) },
            error = error?.let { bounded(it, MAX_SHORT_TEXT_LENGTH) },
            interpretation = interpretation?.let { bounded(it, MAX_LONG_TEXT_LENGTH) },
            origin = bounded(origin, MAX_SHORT_TEXT_LENGTH),
            durationMs = durationMs?.coerceAtLeast(0.0),
            truncatedBytes = data.size - retained,
        ))
        while (events.size > maxEvents) events.removeFirst()
    }

    private fun enforceExportLimit(text: String) {
        require(text.toByteArray(StandardCharsets.UTF_8).size <= maxExportBytes) {
            "Diagnostic trace exceeds the ${maxExportBytes}-byte export limit"
        }
    }

    private fun AndroidTraceSession.toJson() = JSONObject()
        .put("id", id)
        .put("device_name", deviceName)
        .put("vendor_id", vendorId)
        .put("product_id", productId)
        .put("driver", "FTDI")
        .put("baudrate", baudrate)
        .put("data_bits", dataBits)
        .put("parity", parity)
        .put("stop_bits", stopBits)
        .put("local_echo", localEcho)
        .putNullable("ecu_part_number", ecuPartNumber)
        .putNullable("ecu_component", ecuComponent)
        .putNullable("ecu_software_version", ecuSoftwareVersion)
        .put("extra_identification_present", extraIdentificationPresent)

    private fun AndroidTraceEvent.toJson() = JSONObject()
        .put("sequence", sequence)
        .putNullable("session_id", sessionId)
        .put("timestamp", timestamp)
        .put("monotonic", monotonic)
        .put("direction", direction)
        .put("phase", phase)
        .put("data_hex", dataHex)
        .putNullable("result", result)
        .putNullable("error", error)
        .putNullable("interpretation", interpretation)
        .put("origin", origin)
        .putNullable("duration_ms", durationMs)
        .put("truncated_bytes", truncatedBytes)

    private fun JSONObject.putNullable(key: String, value: Any?): JSONObject =
        put(key, value ?: JSONObject.NULL)

    private fun Double.csv(): String = toString()

    private fun csvField(value: String): String = if (value.any { it == ',' || it == '"' || it == '\r' || it == '\n' }) {
        "\"${value.replace("\"", "\"\"")}\""
    } else {
        value
    }

    private fun bounded(text: String, maxLength: Int): String = text.take(maxLength)

    private fun defaultOrigin(direction: String): String = when (direction) {
        "tx" -> "usb_serial_write_call"
        "rx" -> "usb_serial_read_result"
        "control" -> "software_control_request"
        "state" -> "application_state"
        "identity", "tx_frame", "rx_frame", "ack", "rx_consumed" -> "protocol_decode"
        else -> "software_exception"
    }

    companion object {
        const val SCHEMA = "fis-afn-diagnostic-trace/v1"
        const val DEFAULT_MAX_EVENTS = 2_000
        const val DEFAULT_MAX_EVENT_BYTES = 256
        const val DEFAULT_MAX_EXPORT_BYTES = 4 * 1024 * 1024
        private const val MAX_SESSIONS = 32
        private const val MAX_DEVICE_NAME_LENGTH = 128
        private const val MAX_IDENTITY_LENGTH = 128
        private const val MAX_SHORT_TEXT_LENGTH = 128
        private const val MAX_LONG_TEXT_LENGTH = 512
        private val BYTE_HEX = Array(256) { it.toString(16).padStart(2, '0') }
        private val CSV_FIELDS = listOf(
            "timestamp", "monotonic", "direction", "phase", "data_hex", "result", "error", "interpretation",
            "sequence", "session_id", "origin", "duration_ms", "truncated_bytes", "device_name",
            "vendor_id", "product_id", "baudrate", "data_bits", "parity", "stop_bits", "local_echo",
            "ecu_part_number", "ecu_component", "ecu_software_version", "extra_identification_present",
            "electrical_waveform_measured",
        )
    }
}
