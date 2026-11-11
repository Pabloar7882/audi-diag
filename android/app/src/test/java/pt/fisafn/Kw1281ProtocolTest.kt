package pt.fisafn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.ArrayDeque

class Kw1281ProtocolTest {
    @Test
    fun slowInitUsesSevenDataBitsOddParityAndStopBit() {
        assertEquals(listOf(0, 1, 0, 0, 0, 0, 0, 0, 0, 1), buildKw1281SlowInitBits(0x01))
    }

    @Test
    fun receivesStartupIdentityThenAllowsOneExplicitGroupRead() {
        val identity = block(0, 0xF6, "038906018AF".toByteArray())
        val ackToIdentity = block(1, 0x09, byteArrayOf())
        val measurement = block(4, 0xE7, byteArrayOf(0x0C, 0x10, 0x20))
        val ackToMeasurement = block(5, 0x09, byteArrayOf())
        val transport = ScriptedTransport(
            bytes(0x55, 0x01, 0x8A) + identity + acks(ackToIdentity) + block(2, 0x09, byteArrayOf()) +
                acks(block(3, 0x29, byteArrayOf(0x01))) + measurement + acks(ackToMeasurement) +
                block(6, 0x09, byteArrayOf()),
        )
        val session = Kw1281ProtocolSession(transport, sleeper = {})

        assertEquals("038906018AF", session.identify().partNumber)
        assertEquals("0c1020", session.readGroup(1).raw)
        assertEquals(listOf(1), session.requestedGroups)
        assertEquals(7, session.nextCounter)
        assertTrue(transport.writes.map { it[0].toInt() and 0xFF }.windowed(4)
            .any { it == listOf(0x03, 0x01, 0x09, 0x03) })
        assertFalse(transport.writes.any { it.contentEquals(bytes(0x00)) })
    }

    @Test
    fun identificationKeepsPartComponentAndExtraFieldsSeparate() {
        val first = block(0, 0xF6, byteArrayOf(0xB0.toByte()) + "38906 018AF".toByteArray())
        val component = block(2, 0xF6, "EDC15VM+".toByteArray())
        val version = block(4, 0xF6, "0001".toByteArray())
        val extra = block(8, 0xF6, "extra data".toByteArray())
        val transport = ScriptedTransport(
            bytes(0x55, 0x01, 0x8A) + first + acks(block(1, 0x09, byteArrayOf())) +
                component + acks(block(3, 0x09, byteArrayOf())) + version +
                acks(block(5, 0x09, byteArrayOf())) + block(6, 0x09, byteArrayOf()) +
                acks(block(7, 0x00, byteArrayOf())) + extra +
                acks(block(9, 0x09, byteArrayOf())) + block(10, 0x09, byteArrayOf()),
        )

        val identity = Kw1281ProtocolSession(transport, sleeper = {}).identify()
        assertEquals("038906 018AF", identity.partNumber)
        assertEquals("EDC15VM+", identity.component)
        assertEquals("0001", identity.softwareVersion)
        assertEquals("extra data", identity.extraIdentification)
    }

    @Test
    fun continuationFlagInLaterAsciiBlockRequestsExtraIdentitySegment() {
        val partNumber = block(0, 0xF6, "038906018AF".toByteArray())
        val component = block(2, 0xF6, bytes(0xC5) + "DC15VM+".toByteArray())
        val extra = block(6, 0xF6, "extra data".toByteArray())
        val transport = ScriptedTransport(
            bytes(0x55, 0x01, 0x8A) + partNumber + acks(block(1, 0x09, byteArrayOf())) +
                component + acks(block(3, 0x09, byteArrayOf())) + block(4, 0x09, byteArrayOf()) +
                acks(block(5, 0x00, byteArrayOf())) + extra +
                acks(block(7, 0x09, byteArrayOf())) + block(8, 0x09, byteArrayOf()),
        )

        val identity = Kw1281ProtocolSession(transport, sleeper = {}).identify()

        assertEquals("038906018AF", identity.partNumber)
        assertEquals("EDC15VM+", identity.component)
        assertEquals("extra data", identity.extraIdentification)
    }

    @Test
    fun keepaliveUsesOnlyAcknowledgeBlocks() {
        val identity = block(0, 0xF6, "038906018AF".toByteArray())
        val transport = ScriptedTransport(
            bytes(0x55, 0x01, 0x8A) + identity + acks(block(1, 0x09, byteArrayOf())) +
                block(2, 0x09, byteArrayOf()) + acks(block(3, 0x09, byteArrayOf())) +
                block(4, 0x09, byteArrayOf()),
        )
        val session = Kw1281ProtocolSession(transport, sleeper = {})
        session.identify()

        session.keepAlive()

        assertTrue(transport.writes.any { it.contentEquals(bytes(0x09)) })
        assertFalse(transport.writes.any { it.contentEquals(bytes(0x29)) })
    }

    @Test
    fun requiresIdentificationAndRejectsGroupsOutsideAllowList() {
        val noIoTransport = ScriptedTransport(byteArrayOf())
        val uninitialized = Kw1281ProtocolSession(noIoTransport, sleeper = {})
        val beforeIdentify = runCatching { uninitialized.readGroup(1) }.exceptionOrNull()
        assertEquals("ecu_not_identified", (beforeIdentify as Kw1281Exception).code)

        val identity = block(0, 0xF6, "038906018AF".toByteArray())
        val transport = ScriptedTransport(
            bytes(0x55, 0x01, 0x8A) + identity + acks(block(1, 0x09, byteArrayOf())) + block(2, 0x09, byteArrayOf()),
        )
        val session = Kw1281ProtocolSession(transport, sleeper = {})
        session.identify()
        val writeCount = transport.writes.size
        assertEquals("group_not_allowed", runCatching { session.readGroup(2) }
            .exceptionOrNull()?.let { (it as Kw1281Exception).code })
        assertEquals(writeCount, transport.writes.size)
        assertTrue(noIoTransport.writes.isEmpty())
    }

    private class ScriptedTransport(response: ByteArray) : Kw1281ByteTransport {
        override val localEcho = false
        private val rx = ArrayDeque<Byte>().apply { response.forEach(::addLast) }
        val writes = mutableListOf<ByteArray>()
        override fun initSlow(address: Int, bitTimeMs: Long) = Unit
        override fun write(data: ByteArray) { writes += data.copyOf() }
        override fun read(size: Int, timeoutMs: Int): ByteArray {
            if (rx.isEmpty()) return byteArrayOf()
            return ByteArray(minOf(size, rx.size)) { rx.removeFirst() }
        }
        override fun close() = Unit
    }

    companion object {
        private fun bytes(vararg values: Int) = ByteArray(values.size) { values[it].toByte() }
        private fun block(counter: Int, title: Int, data: ByteArray): ByteArray {
            val body = bytes(counter, title) + data + bytes(0x03)
            return bytes(body.size) + body
        }
        private fun acks(frame: ByteArray): ByteArray = ByteArray(frame.size - 1) { (frame[it].toInt() xor 0xFF).toByte() }
    }
}
