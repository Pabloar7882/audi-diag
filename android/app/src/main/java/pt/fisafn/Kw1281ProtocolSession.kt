package pt.fisafn

import java.util.ArrayDeque
import java.util.Locale

internal interface Kw1281ByteTransport {
    val localEcho: Boolean
    fun initSlow(address: Int, bitTimeMs: Long)
    fun write(data: ByteArray)
    fun read(size: Int, timeoutMs: Int): ByteArray
    fun close()
    fun setPhase(phase: String) = Unit
    fun setBreakObserver(observer: ((asserted: Boolean, phase: String, result: String) -> Unit)?) = Unit
    fun recordProtocolEvent(
        direction: String,
        phase: String,
        data: ByteArray = byteArrayOf(),
        result: String? = null,
        error: String? = null,
        interpretation: String? = null,
    ) = Unit
}

internal class Kw1281Exception(val code: String, message: String) : Exception(message)

internal data class Kw1281Identity(
    val partNumber: String,
    val component: String? = null,
    val softwareVersion: String? = null,
    val extraIdentification: String? = null,
)

internal class Kw1281ProtocolSession(
    private val transport: Kw1281ByteTransport,
    private val sleeper: (Long) -> Unit = { Thread.sleep(it) },
) {
    private val rx = ArrayDeque<Byte>()
    private var counter: Int? = null
    private var identified = false
    private var operation = "idle"
    private var phase = "idle"
    val requestedGroups = mutableListOf<Int>()
    val nextCounter: Int? get() = counter

    fun identify(): Kw1281Identity {
        operation = "identify"
        rx.clear()
        counter = null
        identified = false
        setPhase("slow_init")
        transport.initSlow(0x01, 200)
        setPhase("sync_keywords")
        val keyword = readExact(3)
        if (u8(keyword[0]) != 0x55) throw Kw1281Exception("sync_invalid", "KW1281 sync byte is invalid")
        val version = ((u8(keyword[2]) and 0x7F) shl 7) or (u8(keyword[1]) and 0x7F)
        transport.recordProtocolEvent("rx_keyword", phase, keyword, "sync=0x55; protocol=$version")
        sleeper(25)
        setPhase("keyword_complement")
        writeByte(u8(keyword[2]) xor 0xFF)
        if (version != 1281) {
            throw Kw1281Exception("protocol_unsupported", "ECU negotiated protocol $version; KW1281 is required")
        }

        val identityBlocks = mutableListOf<String>()
        val extraBlocks = mutableListOf<String>()
        setPhase("identity_response")
        var response = receiveBlocks()
        if (response.second == NAK) throw Kw1281Exception("identity_rejected", "ECU rejected identification")
        var identity = decodeIdentity(response.first)
        if (identity.first.isEmpty() || identity.first.first().isEmpty()) {
            throw Kw1281Exception("identity_invalid", "ECU sent no identity text")
        }
        identityBlocks += identity.first
        var moreAvailable = identity.second
        repeat(MAX_IDENT_SEGMENTS - 1) {
            if (!moreAvailable) return@repeat
            sendBlock(READ_IDENT)
            response = receiveBlocks()
            if (response.second == NAK) throw Kw1281Exception("identity_rejected", "ECU rejected ReadIdent")
            identity = decodeIdentity(response.first)
            if (identity.first.isEmpty()) throw Kw1281Exception("identity_invalid", "ECU sent an empty identity segment")
            extraBlocks += identity.first
            moreAvailable = identity.second
        }
        if (moreAvailable) throw Kw1281Exception("identity_too_long", "ECU identity exceeded the segment limit")
        val partNumber = identityBlocks.first().trim('\u0000', ' ', '\r', '\n')
        if (partNumber.isEmpty()) throw Kw1281Exception("identity_invalid", "ECU identity text is empty")
        identified = true
        val result = Kw1281Identity(
            partNumber = partNumber,
            component = identityBlocks.getOrNull(1)?.trim()?.takeIf { it.isNotEmpty() },
            softwareVersion = identityBlocks.getOrNull(2)?.trim()?.takeIf { it.isNotEmpty() },
            extraIdentification = extraBlocks.joinToString(" ").trim().takeIf { it.isNotEmpty() },
        )
        transport.recordProtocolEvent(
            direction = "identity",
            phase = "identity_decode",
            result = "part_number=${result.partNumber}; extra_identification_present=${result.extraIdentification != null}",
            interpretation = "Extra ECU identification text is not copied to the structured trace; raw RX bytes may contain personal vehicle identifiers",
        )
        setPhase("identified_idle")
        return result
    }

    fun keepAlive() {
        operation = "keepalive"
        if (!identified) throw Kw1281Exception("ecu_not_identified", "Identify the ECU before keeping the session alive")
        setPhase("keepalive_ack_tx")
        sendBlock(ACK)
        setPhase("keepalive_ack_rx")
        val response = receiveBlocks()
        if (response.first.isNotEmpty() || response.second != ACK) {
            throw Kw1281Exception("keepalive_invalid", "ECU did not acknowledge the KW1281 keep-alive block")
        }
        transport.recordProtocolEvent("ack", "keepalive", result = "ECU ACK received")
        setPhase("identified_idle")
    }

    fun readGroup(group: Int): DiagnosticValue {
        operation = "group_${group.toString().padStart(3, '0')}"
        if (!identified) throw Kw1281Exception("ecu_not_identified", "Identify the ECU before reading a group")
        if (!PhysicalGroupPolicy.isAllowed(group)) throw Kw1281Exception("group_not_allowed", "Only groups 001, 003 and 011 are enabled")
        setPhase("group_request_tx")
        sendBlock(READ_GROUP, byteArrayOf(group.toByte()))
        setPhase("group_response_rx")
        val response = receiveBlocks()
        if (response.second == NAK) throw Kw1281Exception("group_rejected", "ECU rejected group ${group.toString().padStart(3, '0')}")
        val block = response.first.firstOrNull { u8(it[1]) == MEASUREMENTS }
            ?: throw Kw1281Exception("group_block_invalid", "ECU did not return a measurement block")
        if (block.size < 4) throw Kw1281Exception("group_block_invalid", "KW1281 measurement block is truncated")
        requestedGroups += group
        transport.recordProtocolEvent(
            direction = "rx_measurements",
            phase = "group_${group.toString().padStart(3, '0')}",
            data = block,
            result = "raw measurement block",
        )
        setPhase("identified_idle")
        val raw = block.copyOfRange(2, block.size - 1).joinToString("") { "%02x".format(Locale.ROOT, u8(it)) }
        return DiagnosticValue(
            key = "group_${group.toString().padStart(3, '0')}_raw",
            value = null,
            unit = "raw bytes",
            freshness = "fresh",
            source = "live",
            raw = raw,
        )
    }

    private fun decodeIdentity(blocks: List<ByteArray>): Pair<List<String>, Boolean> {
        val fields = mutableListOf<String>()
        var more = false
        blocks.filter { u8(it[1]) == ASCII }.forEach { block ->
            val data = block.copyOfRange(2, block.size - 1)
            if (data.isNotEmpty()) {
                more = more || (u8(data[0]) and 0x80 != 0)
                fields += data.map { (u8(it) and 0x7F).toChar() }.joinToString("")
                    .trim('\u0000', ' ', '\r', '\n')
            }
        }
        return fields to more
    }

    private fun receiveBlocks(): Pair<List<ByteArray>, Int> {
        val blocks = mutableListOf<ByteArray>()
        repeat(MAX_RESPONSE_BLOCKS) {
            val block = readBlock()
            val title = u8(block[1])
            if (title == ACK || title == NAK) {
                transport.recordProtocolEvent(
                    direction = if (title == ACK) "ack" else "nak",
                    phase = phase,
                    data = block,
                    result = if (title == ACK) "terminal ACK" else "terminal NAK",
                )
                return blocks to title
            }
            blocks += block
            setPhase("block_byte_ack_tx")
            sendBlock(ACK)
        }
        throw Kw1281Exception("block_sequence_too_long", "ECU response exceeded the block limit")
    }

    private fun readBlock(): ByteArray {
        setPhase("block_length_rx")
        val length = u8(readExact(1)[0])
        if (length < 3) throw Kw1281Exception("block_length_invalid", "KW1281 block length is invalid")
        setPhase("block_length_complement_tx")
        writeByte(length xor 0xFF)
        val payload = ByteArray(length)
        for (index in payload.indices) {
            setPhase("block_payload_rx")
            payload[index] = readExact(1)[0]
            if (index < payload.lastIndex) {
                setPhase("block_byte_complement_tx")
                writeByte(u8(payload[index]) xor 0xFF)
            }
        }
        val receivedCounter = u8(payload[0])
        val expected = counter
        if (expected != null && receivedCounter != expected) {
            throw Kw1281Exception("block_counter_invalid", "KW1281 block counter mismatch")
        }
        counter = (receivedCounter + 1) and 0xFF
        if (u8(payload.last()) != END) throw Kw1281Exception("block_end_invalid", "KW1281 block has no end byte")
        transport.recordProtocolEvent(
            direction = "rx_frame",
            phase = phase,
            data = byteArrayOf(length.toByte()) + payload,
            result = "counter=$receivedCounter; title=0x${u8(payload[1]).toString(16).padStart(2, '0')}; ETX=valid",
        )
        return payload
    }

    private fun sendBlock(title: Int, data: ByteArray = byteArrayOf()) {
        val blockCounter = counter ?: throw Kw1281Exception("block_counter_unset", "ECU has not initialized the KW1281 counter")
        val payload = byteArrayOf(blockCounter.toByte(), title.toByte()) + data + byteArrayOf(END.toByte())
        val frame = byteArrayOf(payload.size.toByte()) + payload
        transport.recordProtocolEvent(
            direction = "tx_frame",
            phase = phase,
            data = frame,
            result = "counter=$blockCounter; title=0x${title.toString(16).padStart(2, '0')}; ETX=0x03",
            interpretation = "KW1281 frame representation has length, counter, title, data and ETX; this implementation does not define a separate block checksum field",
        )
        frame.forEachIndexed { index, byte ->
            val value = u8(byte)
            writeByte(value)
            if (index < frame.lastIndex) {
                val ack = u8(readExact(1)[0])
                if (ack != (value xor 0xFF)) {
                    transport.recordProtocolEvent("error", "byte_complement", byteArrayOf(ack.toByte()), "expected=0x${(value xor 0xFF).toString(16)}", "byte_ack_invalid")
                    throw Kw1281Exception("byte_ack_invalid", "KW1281 byte complement is invalid")
                }
                transport.recordProtocolEvent("ack", "byte_complement", byteArrayOf(ack.toByte()), "complement=valid")
            }
        }
        counter = (blockCounter + 1) and 0xFF
    }

    private fun writeByte(value: Int) {
        val activePhase = phase
        transport.write(byteArrayOf(value.toByte()))
        if (transport.localEcho) {
            setPhase("tx_echo_rx")
            val echo = u8(readExact(1)[0])
            if (echo != value) {
                transport.recordProtocolEvent("error", "tx_echo", byteArrayOf(echo.toByte()), "expected=0x${value.toString(16)}", "tx_echo_invalid")
                throw Kw1281Exception("tx_echo_invalid", "K-Line transmit echo mismatch")
            }
            transport.recordProtocolEvent("ack", "tx_echo", byteArrayOf(echo.toByte()), "echo=valid")
            setPhase(activePhase)
        }
    }

    private fun readExact(size: Int): ByteArray {
        while (rx.size < size) {
            val chunk = transport.read(size - rx.size, READ_TIMEOUT_MS)
            if (chunk.isEmpty()) throw Kw1281Exception("timeout", "KW1281 response timed out")
            chunk.forEach(rx::addLast)
        }
        return ByteArray(size) { rx.removeFirst() }
    }

    private fun setPhase(value: String) {
        phase = value
        transport.setPhase("$operation:$value")
    }

    private fun u8(value: Byte): Int = value.toInt() and 0xFF

    companion object {
        private const val ACK = 0x09
        private const val NAK = 0x0A
        private const val ASCII = 0xF6
        private const val MEASUREMENTS = 0xE7
        private const val READ_IDENT = 0x00
        private const val READ_GROUP = 0x29
        private const val END = 0x03
        private const val READ_TIMEOUT_MS = 1000
        private const val MAX_RESPONSE_BLOCKS = 32
        private const val MAX_IDENT_SEGMENTS = 16
    }
}
