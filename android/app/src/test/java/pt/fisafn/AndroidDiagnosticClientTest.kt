package pt.fisafn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.ArrayDeque
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class AndroidDiagnosticClientTest {
    @Test
    fun permissionFlowIgnoresStaleResultsAndNeverConnectsAfterDenial() {
        val flow = UsbPermissionFlow()

        assertEquals(UsbPermissionFlow.Outcome.REQUEST_PERMISSION, flow.begin("usb-a", false))
        assertEquals(UsbPermissionFlow.Outcome.IGNORED, flow.permissionResult("usb-b", true))
        assertEquals(UsbPermissionFlow.Outcome.PERMISSION_DENIED, flow.permissionResult("usb-a", false))
        assertEquals(UsbPermissionFlow.Outcome.CONNECT, flow.begin("usb-a", true))
        assertEquals(UsbPermissionFlow.Outcome.IGNORED, flow.detached("usb-a"))
    }

    @Test
    fun permissionGrantConnectsOnlyThePendingDevice() {
        val flow = UsbPermissionFlow()
        flow.begin("usb-a", false)
        assertEquals(UsbPermissionFlow.Outcome.CONNECT, flow.permissionResult("usb-a", true))
        assertEquals(UsbPermissionFlow.Outcome.IGNORED, flow.permissionResult("usb-a", true))
    }

    @Test
    fun permissionGrantPreservesAdapterOnlyActionForTheRequestedDevice() {
        val flow = UsbPermissionFlow()
        assertEquals(
            UsbPermissionFlow.Outcome.REQUEST_PERMISSION,
            flow.begin("usb-a", false, UsbPermissionFlow.Action.OPEN_ADAPTER_ONLY),
        )
        assertEquals(UsbPermissionFlow.Outcome.IGNORED, flow.permissionResult("usb-b", true))
        assertEquals(UsbPermissionFlow.Outcome.OPEN_ADAPTER, flow.permissionResult("usb-a", true))
        assertEquals(
            UsbPermissionFlow.Outcome.OPEN_ADAPTER,
            flow.begin("usb-a", true, UsbPermissionFlow.Action.OPEN_ADAPTER_ONLY),
        )
    }

    @Test
    fun detachingPendingDeviceCancelsPermissionFlow() {
        val flow = UsbPermissionFlow()
        flow.begin("usb-a", false)
        assertEquals(UsbPermissionFlow.Outcome.DETACHED, flow.detached("usb-a"))
        assertEquals(UsbPermissionFlow.Outcome.IGNORED, flow.permissionResult("usb-a", true))
    }

    @Test
    fun adapterCanOpenAndCloseWithoutStartingKw1281() {
        val transport = ScriptedTransport(byteArrayOf())
        val client = AndroidDiagnosticClient { _, _, _ -> transport }
        val generation = client.beginConnection()

        val state = client.openAdapter("usb-a", 10400, false, generation)

        assertEquals("adapter_open", state.status)
        assertEquals(null, state.ecu)
        assertTrue(client.isAdapterOpen())
        assertFalse(client.isConnected())
        assertEquals(0, transport.slowInitCalls)
        assertTrue(transport.writes.isEmpty())

        client.close()

        assertFalse(client.isAdapterOpen())
        assertTrue(transport.closed)
    }

    @Test
    fun foregroundCancellationPreventsTheNextProtocolWrite() {
        var foreground = true
        val transport = ScriptedTransport(identityResponse())
        val trace = AndroidDiagnosticTrace()
        val client = AndroidDiagnosticClient(
            trace = trace,
            operationAllowed = { foreground },
            transportFactory = { _, _, _ -> transport },
        )
        client.connect("usb-a", 10400, false)
        val writesBeforePause = transport.writes.size

        foreground = false
        val error = runCatching { client.keepAlive() }.exceptionOrNull()

        assertEquals("operation_cancelled", (error as? Kw1281Exception)?.code)
        assertEquals(writesBeforePause, transport.writes.size)
        assertFalse(client.isConnected())
        assertTrue(trace.snapshot().events.any { it.error == "operation_cancelled" })
    }

    @Test
    fun clientTraceContainsSoftwareIoProtocolFramesIdentityAndConfiguration() {
        val trace = AndroidDiagnosticTrace()
        val sessionId = trace.beginSession("/dev/bus/usb/001/002", 0x0403, 0x6001, 10400, false)
        val groupResponse = acks(block(3, 0x29, bytes(1))) +
            block(4, 0xE7, bytes(0x0C, 0x10, 0x20)) +
            acks(block(5, 0x09, byteArrayOf())) + block(6, 0x09, byteArrayOf())
        val client = AndroidDiagnosticClient(
            trace = trace,
            transportFactory = { _, _, _ -> ScriptedTransport(identityResponse() + groupResponse) },
        )

        client.connect("/dev/bus/usb/001/002", 10400, false)
        client.readGroup(1)

        val snapshot = trace.snapshot()
        val events = snapshot.events
        val session = snapshot.sessions.single()
        assertEquals(sessionId, session.id)
        assertEquals("038906018AF", session.ecuPartNumber)
        assertTrue(events.any { it.direction == "control" && it.phase == "slow_init" })
        assertTrue(events.any { it.direction == "rx_keyword" && it.dataHex == "55018a" })
        assertTrue(events.any { it.direction == "rx_frame" && it.origin == "protocol_decoder" })
        assertTrue(events.any {
            it.direction == "rx_frame" && it.dataHex.startsWith("06") && it.dataHex.substring(4, 6) == "e7"
        })
        assertTrue(events.any { it.direction == "rx_measurements" && it.phase == "group_001" })
        assertTrue(events.any { it.direction == "tx_frame" && it.origin == "protocol_encoder" })
        assertTrue(events.any { it.direction == "identity" && it.origin == "protocol_decoder" })
        assertTrue(events.all { it.sessionId == sessionId })
        assertEquals(events.map { it.sequence }.sorted(), events.map { it.sequence })
    }

    @Test
    fun staleAdapterOpenClosesLateTransportAfterDisconnect() {
        val factoryStarted = CountDownLatch(1)
        val releaseFactory = CountDownLatch(1)
        val opened = ScriptedTransport(byteArrayOf())
        val client = AndroidDiagnosticClient { _, _, _ ->
            factoryStarted.countDown()
            check(releaseFactory.await(2, TimeUnit.SECONDS))
            opened
        }
        val generation = client.beginConnection()
        val result = AtomicReference<Result<DiagnosticState>>()
        val worker = Thread {
            result.set(runCatching { client.openAdapter("usb-a", 10400, false, generation) })
        }
        worker.start()
        try {
            assertTrue(factoryStarted.await(1, TimeUnit.SECONDS))
            client.disconnect()
            releaseFactory.countDown()
            worker.join(1000)

            assertFalse(worker.isAlive)
            assertTrue(result.get().isFailure)
            assertTrue(opened.closed)
            assertFalse(client.isAdapterOpen())
        } finally {
            releaseFactory.countDown()
            worker.join(1000)
            client.close()
        }
    }

    @Test
    fun clientIdentifiesThenReturnsOnlyExplicitRawGroupSamplesAndCloses() {
        val startup = block(0, 0xF6, "038906018AF".toByteArray())
        val opened = mutableListOf<ScriptedTransport>()
        val client = AndroidDiagnosticClient { _, _, _ ->
            val transport = ScriptedTransport(
                bytes(0x55, 0x01, 0x8A) + startup + acks(block(1, 0x09, byteArrayOf())) +
                    block(2, 0x09, byteArrayOf()) + acks(block(3, 0x09, byteArrayOf())) +
                    block(4, 0x09, byteArrayOf()) + acks(block(5, 0x29, byteArrayOf(0x01))) +
                    block(6, 0xE7, byteArrayOf(0x0C, 0x10, 0x20)) + acks(block(7, 0x09, byteArrayOf())) +
                    block(8, 0x09, byteArrayOf()),
            )
            opened += transport
            transport
        }

        val identified = client.connect("usb-a", 10400, true)
        client.keepAlive()
        val sample = client.readGroup(1)

        assertEquals("identified", identified.status)
        assertEquals("live", identified.source)
        assertEquals("unavailable", identified.freshness)
        assertEquals("038906018AF", identified.ecu)
        assertEquals(null, identified.ecuComponent)
        assertEquals("group_001_raw", sample.values.single().key)
        assertEquals("038906018AF", sample.ecu)
        assertEquals("0c1020", sample.values.single().raw)
        assertEquals(null, sample.values.single().value)
        assertEquals("live", sample.values.single().source)
        assertEquals("fresh", sample.freshness)
        assertTrue(opened.single().writes.map { it[0].toInt() and 0xFF }.windowed(2)
            .any { it == listOf(0x29, 0x01) })

        client.close()
        assertTrue(opened.single().closed)
        assertFalse(opened.single().writes.any { it.contentEquals(byteArrayOf(0x2E)) })
    }

    @Test
    fun groupTimeoutClosesSessionBeforeAnotherReadCanBeAttempted() {
        val startup = block(0, 0xF6, "038906018AF".toByteArray())
        var opened: ScriptedTransport? = null
        val client = AndroidDiagnosticClient { _, _, _ ->
            ScriptedTransport(bytes(0x55, 0x01, 0x8A) + startup +
                acks(block(1, 0x09, byteArrayOf())) + block(2, 0x09, byteArrayOf()))
                .also { opened = it }
        }
        client.connect("usb-a", 10400, true)

        val failure = runCatching { client.readGroup(1) }.exceptionOrNull()

        assertEquals("timeout", (failure as Kw1281Exception).code)
        assertFalse(client.isConnected())
        assertTrue(opened!!.closed)
        assertEquals("ecu_not_identified", runCatching { client.readGroup(1) }
            .exceptionOrNull()?.let { (it as Kw1281Exception).code })
    }

    @Test
    fun disconnectClosesTransportWhileIdentificationIsBlocked() {
        val readStarted = CountDownLatch(1)
        val releaseRead = CountDownLatch(1)
        val blocked = object : Kw1281ByteTransport {
            override val localEcho = false
            @Volatile var closed = false
            override fun initSlow(address: Int, bitTimeMs: Long) = Unit
            override fun write(data: ByteArray) = Unit
            override fun read(size: Int, timeoutMs: Int): ByteArray {
                readStarted.countDown()
                releaseRead.await(2, TimeUnit.SECONDS)
                return byteArrayOf()
            }
            override fun close() {
                closed = true
                releaseRead.countDown()
            }
        }
        val client = AndroidDiagnosticClient { _, _, _ -> blocked }
        val connectThread = Thread { runCatching { client.connect("usb-a", 10400, false) } }
        connectThread.start()
        assertTrue(readStarted.await(1, TimeUnit.SECONDS))

        client.disconnect()
        connectThread.join(1000)

        assertFalse(connectThread.isAlive)
        assertTrue(blocked.closed)
        assertFalse(client.isConnected())
    }

    @Test
    fun failedIdentificationClosesTheUsbTransportAndKeepsNoSession() {
        var transport: ScriptedTransport? = null
        val client = AndroidDiagnosticClient { _, _, _ ->
            ScriptedTransport(byteArrayOf()).also { transport = it }
        }

        val failure = runCatching { client.connect("usb-a", 10400, true) }.exceptionOrNull()
        assertEquals("timeout", (failure as Kw1281Exception).code)
        assertTrue(transport!!.closed)
        val noSession = runCatching { client.readGroup(1) }.exceptionOrNull()
        assertEquals("ecu_not_identified", (noSession as Kw1281Exception).code)
    }

    @Test
    fun disconnectDuringFactoryOpenClosesLateTransportWithoutPublishingIdentity() {
        val factoryStarted = CountDownLatch(1)
        val releaseFactory = CountDownLatch(1)
        val opened = ScriptedTransport(identityResponse())
        val client = AndroidDiagnosticClient { _, _, _ ->
            factoryStarted.countDown()
            check(releaseFactory.await(2, TimeUnit.SECONDS))
            opened
        }
        val result = AtomicReference<Result<DiagnosticState>>()
        val worker = Thread { result.set(runCatching { client.connect("usb-a", 10400, false) }) }
        worker.start()
        try {
            assertTrue(factoryStarted.await(1, TimeUnit.SECONDS))
            val disconnected = client.disconnect()
            releaseFactory.countDown()
            worker.join(1000)

            assertFalse(worker.isAlive)
            assertEquals("usb_disconnected", (result.get().exceptionOrNull() as? Kw1281Exception)?.code)
            assertEquals(null, disconnected.ecu)
            assertFalse(client.isConnected())
            assertTrue(opened.closed)
            assertTrue(opened.writes.isEmpty())
        } finally {
            releaseFactory.countDown()
            worker.join(1000)
            client.close()
        }
    }

    @Test
    fun staleReadSuccessCannotReturnOldDataAfterReconnect() {
        assertStaleOperationRejected(keepAlive = false)
    }

    @Test
    fun queuedConnectionCancelledBeforeOpenNeverCallsFactory() {
        var factoryOpens = 0
        val client = AndroidDiagnosticClient { _, _, _ ->
            factoryOpens++
            ScriptedTransport(identityResponse())
        }
        val reserved = client.beginConnection()
        client.disconnect()

        val failure = runCatching { client.connect("usb-a", 10400, false, reserved) }.exceptionOrNull()

        assertEquals("usb_disconnected", (failure as? Kw1281Exception)?.code)
        assertEquals(0, factoryOpens)
        assertFalse(client.isConnected())
    }

    @Test
    fun lateFactoryResultCannotReplaceOrCloseNewSession() {
        val factoryStarted = CountDownLatch(1)
        val releaseFactory = CountDownLatch(1)
        val old = ScriptedTransport(identityResponse())
        val replacement = ScriptedTransport(identityResponse())
        val client = AndroidDiagnosticClient { name, _, _ ->
            if (name == "usb-a") {
                factoryStarted.countDown()
                check(releaseFactory.await(2, TimeUnit.SECONDS))
                old
            } else replacement
        }
        val result = AtomicReference<Result<DiagnosticState>>()
        val worker = Thread { result.set(runCatching { client.connect("usb-a", 10400, false) }) }
        worker.start()
        try {
            assertTrue(factoryStarted.await(1, TimeUnit.SECONDS))
            client.disconnect()
            client.connect("usb-b", 10400, false)
            releaseFactory.countDown()
            worker.join(1000)

            assertFalse(worker.isAlive)
            assertEquals("usb_disconnected", (result.get().exceptionOrNull() as? Kw1281Exception)?.code)
            assertTrue(old.closed)
            assertTrue(old.writes.isEmpty())
            assertTrue(client.isConnected())
            assertFalse(replacement.closed)
        } finally {
            releaseFactory.countDown()
            worker.join(1000)
            client.close()
        }
    }

    @Test
    fun staleKeepAliveSuccessCannotCompleteAfterReconnect() {
        assertStaleOperationRejected(keepAlive = true)
    }

    @Test
    fun staleKeepAliveFailureDoesNotCloseReplacementSession() {
        val old = BlockingReadTransport(ScriptedTransport(identityResponse()))
        val replacement = ScriptedTransport(identityResponse())
        val client = AndroidDiagnosticClient { name, _, _ -> if (name == "usb-a") old else replacement }
        client.connect("usb-a", 10400, false)
        old.blockNextRead = true
        val result = AtomicReference<Result<Unit>>()
        val worker = Thread { result.set(runCatching { client.keepAlive() }) }
        worker.start()
        try {
            assertTrue(old.readStarted.await(1, TimeUnit.SECONDS))
            client.disconnect()
            client.connect("usb-b", 10400, false)
            old.releaseRead.countDown()
            worker.join(1000)

            assertFalse(worker.isAlive)
            assertTrue(result.get().isFailure)
            assertTrue(client.isConnected())
            assertFalse(replacement.closed)
        } finally {
            old.releaseRead.countDown()
            worker.join(1000)
            client.close()
        }
    }

    private fun assertStaleOperationRejected(keepAlive: Boolean) {
        val response = if (keepAlive) {
            acks(block(3, 0x09, byteArrayOf())) + block(4, 0x09, byteArrayOf())
        } else {
            acks(block(3, 0x29, byteArrayOf(1))) +
                block(4, 0xE7, bytes(0x0C, 0x10, 0x20)) +
                acks(block(5, 0x09, byteArrayOf())) + block(6, 0x09, byteArrayOf())
        }
        val old = BlockingReadTransport(ScriptedTransport(identityResponse() + response))
        val replacement = ScriptedTransport(identityResponse())
        val client = AndroidDiagnosticClient { name, _, _ -> if (name == "usb-a") old else replacement }
        client.connect("usb-a", 10400, false)
        old.blockNextRead = true
        val result = AtomicReference<Result<Any>>()
        val worker = Thread {
            result.set(runCatching { if (keepAlive) client.keepAlive() else client.readGroup(1) })
        }
        worker.start()
        try {
            assertTrue(old.readStarted.await(1, TimeUnit.SECONDS))
            client.disconnect()
            client.connect("usb-b", 10400, false)
            old.releaseRead.countDown()
            worker.join(1000)

            assertFalse(worker.isAlive)
            assertEquals("usb_disconnected", (result.get().exceptionOrNull() as? Kw1281Exception)?.code)
            assertTrue(client.isConnected())
            assertFalse(replacement.closed)
        } finally {
            old.releaseRead.countDown()
            worker.join(1000)
            client.close()
        }
    }

    private class BlockingReadTransport(private val delegate: ScriptedTransport) : Kw1281ByteTransport by delegate {
        @Volatile var blockNextRead = false
        val readStarted = CountDownLatch(1)
        val releaseRead = CountDownLatch(1)
        override fun read(size: Int, timeoutMs: Int): ByteArray {
            if (blockNextRead) {
                blockNextRead = false
                readStarted.countDown()
                check(releaseRead.await(2, TimeUnit.SECONDS))
            }
            return delegate.read(size, timeoutMs)
        }
    }

    private class ScriptedTransport(response: ByteArray) : Kw1281ByteTransport {
        override val localEcho = false
        private val rx = ArrayDeque<Byte>().apply { response.forEach(::addLast) }
        val writes = mutableListOf<ByteArray>()
        var closed = false
        var slowInitCalls = 0
        override fun initSlow(address: Int, bitTimeMs: Long) { slowInitCalls++ }
        override fun write(data: ByteArray) { writes += data.copyOf() }
        override fun read(size: Int, timeoutMs: Int): ByteArray {
            if (rx.isEmpty()) return byteArrayOf()
            return ByteArray(minOf(size, rx.size)) { rx.removeFirst() }
        }
        override fun close() { closed = true }
    }

    companion object {
        private fun identityResponse() = bytes(0x55, 0x01, 0x8A) +
            block(0, 0xF6, "038906018AF".toByteArray()) +
            acks(block(1, 0x09, byteArrayOf())) + block(2, 0x09, byteArrayOf())
        private fun bytes(vararg values: Int) = ByteArray(values.size) { values[it].toByte() }
        private fun block(counter: Int, title: Int, data: ByteArray): ByteArray {
            val body = bytes(counter, title) + data + bytes(0x03)
            return bytes(body.size) + body
        }
        private fun acks(frame: ByteArray): ByteArray = ByteArray(frame.size - 1) {
            ((frame[it].toInt() and 0xFF) xor 0xFF).toByte()
        }
    }
}
