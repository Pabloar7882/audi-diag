package pt.fisafn

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

class AndroidDiagnosticTraceTest {
    @Test
    fun exportsVersionedJsonWithWallAndMonotonicTimestampsAndEvidenceOrigin() {
        val clock = TestTraceClock()
        val trace = AndroidDiagnosticTrace(clock = clock)
        val session = trace.beginSession("/dev/bus/usb/001/002", 0x0403, 0x6001, 10400, true)
        trace.record(
            direction = "tx",
            phase = "keyword_ack",
            data = byteArrayOf(0x75),
            result = "write_returned",
            origin = "usb_serial_write_call",
            sessionId = session,
        )

        val json = JSONObject(trace.exportJson())
        val event = json.getJSONArray("events").getJSONObject(1)
        val evidence = json.getJSONObject("evidence")

        assertEquals("fis-afn-diagnostic-trace/v1", json.getString("schema"))
        assertEquals("android", json.getString("platform"))
        assertEquals("usb_serial_write_call", event.getString("origin"))
        assertEquals(session, event.getString("session_id"))
        assertEquals(1_800_000_001.0, event.getDouble("timestamp"), 0.0)
        assertEquals(21.0, event.getDouble("monotonic"), 0.0)
        assertFalse(evidence.getBoolean("electrical_waveform_measured"))
        assertEquals(0x0403, json.getJSONArray("sessions").getJSONObject(0).getInt("vendor_id"))
        assertEquals(10400, json.getJSONArray("sessions").getJSONObject(0).getInt("baudrate"))
    }

    @Test
    fun csvPreservesWindowsV1ColumnsAndEscapesText() {
        val trace = AndroidDiagnosticTrace(clock = TestTraceClock())
        trace.record(
            direction = "state",
            phase = "identify",
            result = "started, then \"continued\"",
            origin = "application_state",
        )

        val lines = trace.exportCsv().split("\r\n")

        assertEquals(
            "timestamp,monotonic,direction,phase,data_hex,result,error,interpretation,sequence,session_id,origin,duration_ms,truncated_bytes",
            lines[0].substringBefore(",device_name"),
        )
        assertTrue(lines[1].contains("\"started, then \"\"continued\"\"\""))
        assertTrue(lines[0].startsWith("timestamp,monotonic,direction,phase,data_hex,result,error,interpretation,"))
    }

    @Test
    fun eventAndPayloadLimitsAreVisibleAndOldestEventsAreEvicted() {
        val trace = AndroidDiagnosticTrace(
            clock = TestTraceClock(),
            maxEvents = 2,
            maxEventBytes = 2,
        )
        trace.record("tx", "first", byteArrayOf(0x01))
        trace.record("rx", "second", byteArrayOf(0x02, 0x03, 0x04))
        trace.record("error", "third", error = "timeout")

        val events = JSONObject(trace.exportJson()).getJSONArray("events")

        assertEquals(2, events.length())
        assertEquals(2L, events.getJSONObject(0).getLong("sequence"))
        assertEquals("0203", events.getJSONObject(0).getString("data_hex"))
        assertEquals(1, events.getJSONObject(0).getInt("truncated_bytes"))
    }

    @Test
    fun exportedSnapshotDoesNotChangeWhenNewEventsArrive() {
        val trace = AndroidDiagnosticTrace(clock = TestTraceClock())
        trace.record("state", "before", result = "one")
        val snapshot = trace.snapshot()
        trace.record("state", "after", result = "two")

        assertEquals(1, JSONObject(trace.exportJson(snapshot)).getJSONArray("events").length())
        assertEquals(2, JSONObject(trace.exportJson()).getJSONArray("events").length())
    }

    @Test
    fun concurrentRecordAndExportAlwaysProducesParseableBoundedSnapshots() {
        val trace = AndroidDiagnosticTrace(clock = TestTraceClock(), maxEvents = 128)
        val ready = CountDownLatch(4)
        val start = CountDownLatch(1)
        val workers = (0 until 4).map { worker ->
            Thread {
                ready.countDown()
                check(start.await(2, TimeUnit.SECONDS))
                repeat(100) { index ->
                    trace.record("rx", "worker_$worker", byteArrayOf(index.toByte()))
                    val snapshot = JSONObject(trace.exportJson()).getJSONArray("events")
                    check(snapshot.length() <= 128)
                }
            }.apply { start() }
        }

        assertTrue(ready.await(1, TimeUnit.SECONDS))
        start.countDown()
        workers.forEach { it.join(3000) }

        assertTrue(workers.none { it.isAlive })
        val snapshot: JSONArray = JSONObject(trace.exportJson()).getJSONArray("events")
        assertEquals(128, snapshot.length())
    }

    @Test
    fun oversizedExportFailsBeforeAllocatingAnUnboundedPayload() {
        val trace = AndroidDiagnosticTrace(clock = TestTraceClock(), maxExportBytes = 8)
        repeat(10) { trace.record("rx", "phase", ByteArray(20) { it.toByte() }) }

        assertTrue(runCatching { trace.exportJson() }.isFailure)
    }

    private class TestTraceClock : () -> TraceTimestamp {
        private val tick = AtomicLong()
        override fun invoke(): TraceTimestamp {
            val current = tick.getAndIncrement()
            return TraceTimestamp(
                wallSeconds = 1_800_000_000.0 + current,
                monotonicSeconds = 20.0 + current,
            )
        }
    }
}
