package pt.fisafn

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.ArrayDeque

class FtdiReadBufferTest {
    @Test
    fun oneByteProtocolReadUsesFtdiSafeBufferAndKeepsCoalescedBytes() {
        val received = ArrayDeque<Byte>().apply {
            byteArrayOf(0x55, 0x01, 0x8A.toByte(), 0x08).forEach(::addLast)
        }
        var allocatedSize = 0

        val chunk = readFtdiChunk(1, 1000) { buffer, _ ->
            allocatedSize = buffer.size
            require(buffer.size > 2) { "FTDI read buffer must include its two status bytes" }
            val count = minOf(buffer.size, received.size)
            repeat(count) { buffer[it] = received.removeFirst() }
            count
        }

        assertEquals(64, allocatedSize)
        assertArrayEquals(byteArrayOf(0x55, 0x01, 0x8A.toByte(), 0x08), chunk)
        assertEquals(0, received.size)
    }

    @Test
    fun protocolSessionRetainsExtraBytesReturnedByFtdiReads() {
        val identity = block(0, 0xF6, "038906018AF".toByteArray())
        val measurements = block(4, 0xE7, bytes(0x0C, 0x10, 0x20))
        val transport = FtdiLikeTransport(
            bytes(0x55, 0x01, 0x8A) + identity + acks(block(1, 0x09)) + block(2, 0x09) +
                acks(block(3, 0x29, bytes(0x03))) + measurements +
                acks(block(5, 0x09)) + block(6, 0x09),
        )
        val session = Kw1281ProtocolSession(transport, sleeper = {})

        assertEquals("038906018AF", session.identify().partNumber)
        assertEquals("0c1020", session.readGroup(3).raw)
        assertTrue(transport.requestedSizes.any { it == 1 })
    }

    private class FtdiLikeTransport(response: ByteArray) : Kw1281ByteTransport {
        override val localEcho = false
        private val received = ArrayDeque<Byte>().apply { response.forEach(::addLast) }
        val requestedSizes = mutableListOf<Int>()
        override fun initSlow(address: Int, bitTimeMs: Long) = Unit
        override fun write(data: ByteArray) = Unit
        override fun read(size: Int, timeoutMs: Int): ByteArray {
            requestedSizes += size
            return readFtdiChunk(size, timeoutMs) { buffer, _ ->
                require(buffer.size > 2) { "FTDI read buffer must include its two status bytes" }
                val count = minOf(buffer.size, received.size, 3)
                repeat(count) { buffer[it] = received.removeFirst() }
                count
            }
        }
        override fun close() = Unit
    }

    companion object {
        private fun bytes(vararg values: Int) = ByteArray(values.size) { values[it].toByte() }
        private fun block(counter: Int, title: Int, data: ByteArray = byteArrayOf()): ByteArray {
            val body = bytes(counter, title) + data + bytes(0x03)
            return bytes(body.size) + body
        }
        private fun acks(frame: ByteArray): ByteArray = ByteArray(frame.size - 1) {
            (frame[it].toInt() xor 0xFF).toByte()
        }
    }
}
