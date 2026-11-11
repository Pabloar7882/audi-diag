package pt.fisafn

internal class AndroidDiagnosticClient(
    private val trace: AndroidDiagnosticTrace = AndroidDiagnosticTrace(),
    private val operationAllowed: () -> Boolean = { true },
    private val transportFactory: (deviceName: String, baudrate: Int, localEcho: Boolean) -> Kw1281ByteTransport,
) {
    private val stateLock = Any()
    private var transport: Kw1281ByteTransport? = null
    private var session: Kw1281ProtocolSession? = null
    private var ecu: String? = null
    private var generation = 0L
    private var activeTraceSessionId: String? = null

    fun openAdapter(
        deviceName: String,
        baudrate: Int,
        localEcho: Boolean,
        expectedGeneration: Long = beginConnection(),
        traceSessionId: String? = trace.currentSessionId(),
    ): DiagnosticState {
        synchronized(stateLock) { requireCurrent(expectedGeneration) }
        requireOperationAllowed()
        val opened = try {
            transportFactory(deviceName, baudrate, localEcho)
        } catch (error: Exception) {
            recordFailure("adapter_open", traceSessionId, error)
            throw error
        }
        val traced = TracingKw1281ByteTransport(opened, trace, traceSessionId)
        try {
            synchronized(stateLock) {
                requireCurrent(expectedGeneration)
                requireOperationAllowed()
                transport = traced
                session = null
                activeTraceSessionId = traceSessionId
                clearIdentity()
            }
            trace.record("state", "adapter_open", result = "port opened; no KW1281 command sent", sessionId = traceSessionId)
            return DiagnosticState(
                mode = "diagnostic",
                status = "adapter_open",
                source = "live",
                freshness = "unavailable",
            )
        } catch (error: Exception) {
            synchronized(stateLock) {
                if (generation == expectedGeneration && transport === traced) {
                    generation++
                    transport = null
                    session = null
                    activeTraceSessionId = null
                    clearIdentity()
                }
            }
            runCatching { traced.close() }
            recordFailure("adapter_open", traceSessionId, error)
            throw error
        }
    }

    fun connect(
        deviceName: String,
        baudrate: Int,
        localEcho: Boolean,
        expectedGeneration: Long = beginConnection(),
        traceSessionId: String? = trace.currentSessionId(),
    ): DiagnosticState {
        synchronized(stateLock) { requireCurrent(expectedGeneration) }
        requireOperationAllowed()
        val opened = try {
            transportFactory(deviceName, baudrate, localEcho)
        } catch (error: Exception) {
            recordFailure("transport_open", traceSessionId, error)
            throw error
        }
        lateinit var traced: Kw1281ByteTransport
        val guarded = GenerationGuardedKw1281ByteTransport(
            opened,
            { synchronized(stateLock) { generation == expectedGeneration && transport === traced } },
            operationAllowed,
        )
        traced = TracingKw1281ByteTransport(guarded, trace, traceSessionId)
        try {
            synchronized(stateLock) {
                requireCurrent(expectedGeneration)
                requireOperationAllowed()
                transport = traced
                activeTraceSessionId = traceSessionId
            }
            trace.record("state", "identify", result = "started", sessionId = traceSessionId)
            val protocol = Kw1281ProtocolSession(traced)
            val identity = protocol.identify()
            synchronized(stateLock) {
                requireCurrent(expectedGeneration)
                if (transport !== traced) throw disconnected()
                session = protocol
                ecu = identity.partNumber
            }
            trace.updateIdentity(
                traceSessionId,
                identity.partNumber,
                identity.component,
                identity.softwareVersion,
                identity.extraIdentification != null,
            )
            trace.record("state", "identify", result = "identified", sessionId = traceSessionId)
            return DiagnosticState(
                mode = "diagnostic",
                status = "identified",
                source = "live",
                freshness = "unavailable",
                ecu = identity.partNumber,
                ecuComponent = identity.component,
                ecuSoftwareVersion = identity.softwareVersion,
                ecuExtraIdentification = identity.extraIdentification,
            )
        } catch (error: Exception) {
            synchronized(stateLock) {
                if (generation == expectedGeneration && transport === traced) {
                    generation++
                    transport = null
                    session = null
                    activeTraceSessionId = null
                    clearIdentity()
                }
            }
            runCatching { traced.close() }
            recordFailure("identify", traceSessionId, error)
            throw error
        }
    }

    fun readGroup(group: Int): DiagnosticState {
        val active = synchronized(stateLock) {
            val activeSession = session ?: throw Kw1281Exception("ecu_not_identified", "Identify the ECU before reading a group")
            ActiveSession(generation, activeSession, ecu, activeTraceSessionId)
        }
        trace.record("state", "group_read", result = "group=$group; started", sessionId = active.traceSessionId)
        try {
            val value = active.protocol.readGroup(group)
            synchronized(stateLock) { requireCurrent(active.generation) }
            trace.record("state", "group_read", result = "group=$group; returned", sessionId = active.traceSessionId)
            return DiagnosticState(
                mode = "diagnostic",
                status = "live",
                source = "live",
                freshness = "fresh",
                ecu = active.ecu,
                values = listOf(value),
                history = listOf(value),
            )
        } catch (error: Exception) {
            recordFailure("group_$group", active.traceSessionId, error)
            if (error !is Kw1281Exception || error.code !in NON_FATAL_READ_ERRORS) invalidate(active)
            throw error
        }
    }

    fun keepAlive() {
        val active = synchronized(stateLock) {
            val protocol = session ?: throw Kw1281Exception("ecu_not_identified", "Identify the ECU before keeping the session alive")
            ActiveSession(generation, protocol, ecu, activeTraceSessionId)
        }
        trace.record("state", "keepalive", result = "started", sessionId = active.traceSessionId)
        try {
            active.protocol.keepAlive()
            synchronized(stateLock) { requireCurrent(active.generation) }
            trace.record("state", "keepalive", result = "returned", sessionId = active.traceSessionId)
        } catch (error: Exception) {
            recordFailure("keepalive", active.traceSessionId, error)
            invalidate(active)
            throw error
        }
    }

    fun isConnected(): Boolean = synchronized(stateLock) { session != null && transport != null }
    fun isAdapterOpen(): Boolean = synchronized(stateLock) { transport != null }

    fun disconnect(): DiagnosticState {
        close()
        return DiagnosticState(
            mode = "diagnostic",
            status = "disconnected",
            source = "live",
            freshness = "unavailable",
            error = DiagnosticErrorState("usb_disconnected", "Adaptador USB desligado", recoverable = true),
        )
    }

    fun close() {
        beginConnection()
    }

    // Reserve before queuing I/O so detach also cancels work that has not started opening yet.
    fun beginConnection(): Long {
        val current = synchronized(stateLock) {
            trace.record(
                "state", "session_invalidate", result = "generation advanced",
                sessionId = activeTraceSessionId ?: trace.currentSessionId(),
            )
            generation++
            val active = transport
            transport = null
            session = null
            activeTraceSessionId = null
            clearIdentity()
            active to generation
        }
        runCatching { current.first?.close() }
        return current.second
    }

    private fun invalidate(active: ActiveSession) {
        val current = synchronized(stateLock) {
            if (generation != active.generation || session !== active.protocol) return
            generation++
            trace.record("state", "session_invalidate", result = "protocol operation failed", sessionId = active.traceSessionId)
            val opened = transport
            transport = null
            session = null
            activeTraceSessionId = null
            clearIdentity()
            opened
        }
        runCatching { current?.close() }
    }

    private fun requireCurrent(expectedGeneration: Long) {
        if (generation != expectedGeneration) throw disconnected()
    }

    private fun disconnected() = Kw1281Exception("usb_disconnected", "USB serial connection was closed")

    private fun requireOperationAllowed() {
        if (!operationAllowed()) throw Kw1281Exception("operation_cancelled", "Foreground KW1281 operation was cancelled")
    }

    private data class ActiveSession(
        val generation: Long,
        val protocol: Kw1281ProtocolSession,
        val ecu: String?,
        val traceSessionId: String?,
    )

    private fun clearIdentity() {
        ecu = null
    }

    private fun recordFailure(phase: String, sessionId: String?, error: Exception) {
        val code = (error as? Kw1281Exception)?.code ?: error.javaClass.simpleName.ifBlank { "error" }
        trace.record(
            direction = "error",
            phase = phase,
            error = code,
            result = error.message ?: code,
            interpretation = "Application-level operation failed; recorded data describes software observations",
            origin = "software_exception",
            sessionId = sessionId,
        )
    }

    companion object {
        private val NON_FATAL_READ_ERRORS = setOf("group_not_allowed", "group_rejected")
    }
}

// Tokens are captured when work is queued and checked again on the UI thread.
internal class DiagnosticSessionCallbacks {
    data class Token(val generation: Long, val deviceName: String, val traceSessionId: String? = null)

    private var generation = 0L
    private var active: Token? = null

    @Synchronized
    fun begin(deviceName: String, traceSessionId: String? = null): Token =
        Token(++generation, deviceName, traceSessionId).also { active = it }

    @Synchronized
    fun current(): Token? = active

    @Synchronized
    fun isCurrent(token: Token): Boolean = active == token

    @Synchronized
    fun clear() {
        generation++
        active = null
    }

    @Synchronized
    fun runIfCurrent(token: Token, action: () -> Unit) {
        if (isCurrent(token)) action()
    }
}

internal class UsbPermissionFlow {
    enum class Action { IDENTIFY_ECU, OPEN_ADAPTER_ONLY }
    enum class Outcome { REQUEST_PERMISSION, CONNECT, OPEN_ADAPTER, PERMISSION_DENIED, DETACHED, IGNORED }

    private var pendingDevice: String? = null
    private var pendingAction = Action.IDENTIFY_ECU

    @Synchronized
    fun begin(
        deviceName: String,
        permissionGranted: Boolean,
        action: Action = Action.IDENTIFY_ECU,
    ): Outcome {
        if (permissionGranted) {
            pendingDevice = null
            pendingAction = Action.IDENTIFY_ECU
            return outcomeFor(action)
        }
        pendingDevice = deviceName
        pendingAction = action
        return Outcome.REQUEST_PERMISSION
    }

    @Synchronized
    fun permissionResult(deviceName: String, granted: Boolean): Outcome {
        if (pendingDevice != deviceName) return Outcome.IGNORED
        pendingDevice = null
        val action = pendingAction
        pendingAction = Action.IDENTIFY_ECU
        return if (granted) outcomeFor(action) else Outcome.PERMISSION_DENIED
    }

    @Synchronized
    fun detached(deviceName: String): Outcome {
        if (pendingDevice != deviceName) return Outcome.IGNORED
        pendingDevice = null
        pendingAction = Action.IDENTIFY_ECU
        return Outcome.DETACHED
    }

    private fun outcomeFor(action: Action) = when (action) {
        Action.IDENTIFY_ECU -> Outcome.CONNECT
        Action.OPEN_ADAPTER_ONLY -> Outcome.OPEN_ADAPTER
    }
}
