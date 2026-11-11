package pt.fisafn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.ArrayDeque

class TracingKw1281ByteTransportTest {
    @Test
    fun slowInitBreakEventsUseCapturedSessionAndObserverIsCleared() {
        val trace = AndroidDiagnosticTrace()
        val originalSession = trace.beginSession("usb-node", 0x0403, 0x6001, 10400, false)
        val delegate = FixtureTransport(byteArrayOf())
        val traced = TracingKw1281ByteTransport(delegate, trace, originalSession)
        trace.beginSession("usb-node-2", 0x0403, 0x6001, 10400, false)

        traced.initSlow(0x01, 200)

        val breakEvents = trace.snapshot().events.filter { it.phase.startsWith("break_") }
        assertTrue(breakEvents.isNotEmpty())
        assertTrue(breakEvents.all { it.sessionId == originalSession })
        assertEquals(null, delegate.observedBreakCallback)
    }

    @Test
    fun recordsRawIoSlowInitAndSemanticFramesWithoutClaimingElectricalMeasurement() {
        val trace = AndroidDiagnosticTrace(clock = { TraceTimestamp(100.0, 50.0) })
        val session = trace.beginSession("usb-node", 0x0403, 0x6001, 10400, false)
        val delegate = FixtureTransport(byteArrayOf(0x55.toByte(), 0x01.toByte(), 0x8A.toByte()))
        val traced = TracingKw1281ByteTransport(delegate, trace, session)

        traced.setPhase("slow_init")
        traced.initSlow(0x01, 200)
        traced.setPhase("keyword_ack")
        traced.write(byteArrayOf(0x75))
        assertEquals("55018a", traced.read(3, 1000).joinToString("") { "%02x".format(it) })
        traced.recordProtocolEvent(
            "tx_frame", "identify_request",
            byteArrayOf(0x03.toByte(), 0x01.toByte(), 0x00.toByte(), 0x03.toByte()), "complete",
        )

        val events = trace.snapshot().events

        assertTrue(events.any { it.direction == "control" && it.phase == "slow_init" })
        assertTrue(events.any { it.direction == "tx" && it.dataHex == "75" })
        assertTrue(events.any { it.direction == "rx" && it.dataHex == "55018a" })
        assertTrue(events.any { it.direction == "tx_frame" && it.dataHex == "03010003" })
        assertTrue(trace.exportJson().contains("\"electrical_waveform_measured\":false"))
        assertTrue(events.filter { it.phase == "slow_init" }.all { it.origin != "electrical_measurement" })
    }

    @Test
    fun recordsReadAndWriteFailuresWithCodesAndAttemptedBytes() {
        val trace = AndroidDiagnosticTrace(clock = { TraceTimestamp(100.0, 50.0) })
        val delegate = FixtureTransport(byteArrayOf()).apply { failRead = true; failWrite = true }
        val traced = TracingKw1281ByteTransport(delegate, trace, null)

        assertTrue(runCatching { traced.write(byteArrayOf(0x75)) }.isFailure)
        assertTrue(runCatching { traced.read(1, 1000) }.isFailure)

        val failures = trace.snapshot().events.filter { it.direction == "error" }
        assertEquals(2, failures.size)
        assertEquals("usb_write_failed", failures[0].error)
        assertEquals("75", failures[0].dataHex)
        assertEquals("usb_read_failed", failures[1].error)
        assertTrue(trace.exportJson().contains("usb_write_failed"))
    }

    private class FixtureTransport(response: ByteArray) : Kw1281ByteTransport {
        override val localEcho = false
        private val rx = ArrayDeque<Byte>().apply { response.forEach(::addLast) }
        var observedBreakCallback: ((asserted: Boolean, phase: String, result: String) -> Unit)? = null
        var failRead = false
        var failWrite = false
        override fun initSlow(address: Int, bitTimeMs: Long) {
            observedBreakCallback?.invoke(false, "prelude", "requested")
            observedBreakCallback?.invoke(false, "prelude", "transport_call_returned")
        }
        override fun setBreakObserver(observer: ((asserted: Boolean, phase: String, result: String) -> Unit)?) {
            observedBreakCallback = observer
        }
        override fun write(data: ByteArray) {
            if (failWrite) throw Kw1281Exception("usb_write_failed", "fixture write failed")
        }
        override fun read(size: Int, timeoutMs: Int): ByteArray {
            if (failRead) throw Kw1281Exception("usb_read_failed", "fixture read failed")
            if (rx.isEmpty()) return byteArrayOf()
            return ByteArray(minOf(size, rx.size)) { rx.removeFirst() }
        }
        override fun close() = Unit
    }
}
