package pt.fisafn

import java.io.IOException

internal class GenerationGuardedKw1281ByteTransport(
    private val delegate: Kw1281ByteTransport,
    private val isCurrent: () -> Boolean,
    private val isOperationAllowed: () -> Boolean = { true },
) : Kw1281ByteTransport {
    override val localEcho: Boolean get() = delegate.localEcho

    override fun initSlow(address: Int, bitTimeMs: Long) {
        ensureCurrent()
        delegate.initSlow(address, bitTimeMs)
    }

    override fun write(data: ByteArray) {
        ensureCurrent()
        delegate.write(data)
        ensureCurrent()
    }

    override fun read(size: Int, timeoutMs: Int): ByteArray {
        ensureCurrent()
        return delegate.read(size, timeoutMs).also { ensureCurrent() }
    }

    override fun close() = delegate.close()

    override fun setPhase(phase: String) = delegate.setPhase(phase)

    override fun setBreakObserver(observer: ((asserted: Boolean, phase: String, result: String) -> Unit)?) =
        delegate.setBreakObserver(observer)

    override fun recordProtocolEvent(
        direction: String,
        phase: String,
        data: ByteArray,
        result: String?,
        error: String?,
        interpretation: String?,
    ) = delegate.recordProtocolEvent(direction, phase, data, result, error, interpretation)

    private fun ensureCurrent() {
        if (!isCurrent()) throw Kw1281Exception("usb_disconnected", "KW1281 operation was cancelled")
        if (!isOperationAllowed()) throw Kw1281Exception("operation_cancelled", "Foreground KW1281 operation was cancelled")
    }
}

internal class TracingKw1281ByteTransport(
    private val delegate: Kw1281ByteTransport,
    private val trace: AndroidDiagnosticTrace,
    private val sessionId: String?,
) : Kw1281ByteTransport {
    override val localEcho: Boolean get() = delegate.localEcho
    private var phase = "idle"

    override fun setPhase(phase: String) {
        this.phase = phase.take(MAX_PHASE_LENGTH)
        delegate.setPhase(this.phase)
    }

    override fun recordProtocolEvent(
        direction: String,
        phase: String,
        data: ByteArray,
        result: String?,
        error: String?,
        interpretation: String?,
    ) {
        trace.record(
            direction = direction,
            phase = phase,
            data = data,
            result = result,
            error = error,
            interpretation = interpretation,
            origin = when {
                direction.startsWith("tx") -> "protocol_encoder"
                direction.startsWith("rx") || direction == "identity" || direction == "ack" -> "protocol_decoder"
                else -> "protocol_state"
            },
            sessionId = sessionId,
        )
        delegate.recordProtocolEvent(direction, phase, data, result, error, interpretation)
    }

    override fun initSlow(address: Int, bitTimeMs: Long) {
        val started = System.nanoTime()
        trace.record(
            direction = "control",
            phase = "slow_init",
            data = byteArrayOf(address.toByte()),
            result = "requested",
            interpretation = "initSlow invoked; serial BREAK calls are software requests and electrical waveform/timing are unmeasured",
            origin = "software_control_request",
            sessionId = sessionId,
        )
        delegate.setBreakObserver { asserted, breakPhase, result ->
            trace.record(
                direction = "control",
                phase = "break_$breakPhase",
                data = byteArrayOf(if (asserted) 1 else 0),
                result = result,
                interpretation = "BREAK API request/result only; USB adapter/driver call does not prove the physical K-Line waveform",
                origin = if (result == "requested") "software_control_request" else "software_control_result",
                sessionId = sessionId,
            )
        }
        try {
            delegate.initSlow(address, bitTimeMs)
            trace.record(
                direction = "control",
                phase = "slow_init",
                data = byteArrayOf(address.toByte()),
                result = "transport_call_returned; bit_time_ms=$bitTimeMs",
                interpretation = "The transport call returned; no physical BREAK waveform was sampled",
                origin = "software_control_result",
                sessionId = sessionId,
                durationMs = elapsedMs(started),
            )
        } catch (error: Exception) {
            recordFailure("slow_init", byteArrayOf(address.toByte()), error, started)
            throw error
        } finally {
            runCatching { delegate.setBreakObserver(null) }
        }
    }

    override fun write(data: ByteArray) {
        val started = System.nanoTime()
        try {
            delegate.write(data)
            trace.record(
                direction = "tx",
                phase = phase,
                data = data,
                result = "write_call_returned",
                interpretation = "USB serial write returned; wire-level delivery is not measured",
                origin = "usb_serial_write_call",
                sessionId = sessionId,
                durationMs = elapsedMs(started),
            )
        } catch (error: Exception) {
            recordFailure(phase, data, error, started)
            throw error
        }
    }

    override fun read(size: Int, timeoutMs: Int): ByteArray {
        val started = System.nanoTime()
        try {
            val data = delegate.read(size, timeoutMs)
            trace.record(
                direction = "rx",
                phase = phase,
                data = data,
                result = if (data.isEmpty()) "empty_read" else "read_returned",
                interpretation = "Raw byte buffer returned by the USB serial driver",
                origin = "usb_serial_read_result",
                sessionId = sessionId,
                durationMs = elapsedMs(started),
            )
            return data
        } catch (error: Exception) {
            recordFailure(phase, byteArrayOf(), error, started)
            throw error
        }
    }

    override fun close() {
        trace.record("state", "transport_close", result = "requested", origin = "application_state", sessionId = sessionId)
        try {
            delegate.close()
            trace.record("state", "transport_close", result = "returned", origin = "application_state", sessionId = sessionId)
        } catch (error: Exception) {
            recordFailure("transport_close", byteArrayOf(), error, System.nanoTime())
            throw error
        }
    }

    private fun recordFailure(phase: String, data: ByteArray, error: Exception, started: Long) {
        val code = when (error) {
            is Kw1281Exception -> error.code
            is IOException -> "io_error"
            else -> error.javaClass.simpleName.ifBlank { "error" }
        }
        trace.record(
            direction = "error",
            phase = phase,
            data = data,
            result = error.message?.take(MAX_ERROR_MESSAGE_LENGTH),
            error = code,
            origin = "software_exception",
            sessionId = sessionId,
            durationMs = elapsedMs(started),
        )
    }

    private fun elapsedMs(startedNanos: Long): Double =
        ((System.nanoTime() - startedNanos).coerceAtLeast(0L)) / 1_000_000.0

    companion object {
        private const val MAX_PHASE_LENGTH = 128
        private const val MAX_ERROR_MESSAGE_LENGTH = 512
    }
}
