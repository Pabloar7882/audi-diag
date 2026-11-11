package pt.fisafn

import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbManager
import com.hoho.android.usbserial.driver.FtdiSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import java.io.IOException

internal fun readFtdiChunk(
    requestedSize: Int,
    timeoutMs: Int,
    readPort: (ByteArray, Int) -> Int,
): ByteArray {
    if (requestedSize <= 0) return byteArrayOf()
    val bufferSize = maxOf(FTDI_READ_PACKET_BYTES, requestedSize)
        .let { ((it + FTDI_READ_PACKET_BYTES - 1) / FTDI_READ_PACKET_BYTES) * FTDI_READ_PACKET_BYTES }
    val buffer = ByteArray(bufferSize)
    val count = readPort(buffer, timeoutMs)
    if (count !in 0..buffer.size) throw IOException("FTDI returned an invalid read length")
    return if (count > 0) buffer.copyOf(count) else byteArrayOf()
}

internal fun buildKw1281SlowInitBits(address: Int): List<Int> {
    require(address in 0..0x7F) { "KW1281 address must fit seven data bits" }
    val data = (0 until 7).map { (address shr it) and 1 }
    val parity = if (data.sum() % 2 == 0) 1 else 0
    return listOf(0) + data + parity + 1
}

class UsbKLineTransport(
    private val manager: UsbManager,
    override val localEcho: Boolean = true,
    private val operationAllowed: () -> Boolean = { true },
) : Kw1281ByteTransport {
    private var connection: UsbDeviceConnection? = null
    private var port: UsbSerialPort? = null
    private val breakObserver = BreakObserverSlot()

    override fun setBreakObserver(observer: ((asserted: Boolean, phase: String, result: String) -> Unit)?) =
        breakObserver.set(observer)

    @Synchronized
    fun open(device: UsbDevice, baudrate: Int) {
        check(port == null) { "USB serial port is already open" }
        require(manager.hasPermission(device)) { "Permissão USB não concedida" }
        require(baudrate in SUPPORTED_BAUDRATES) { "Velocidade série não suportada" }
        val driver = UsbSerialProber.getDefaultProber().findAllDrivers(manager)
            .filterIsInstance<FtdiSerialDriver>()
            .firstOrNull { it.device.deviceName == device.deviceName }
            ?: throw Kw1281Exception("ftdi_driver_missing", "O dispositivo não foi reconhecido pelo driver FTDI")
        val openedConnection = manager.openDevice(device)
            ?: throw Kw1281Exception("usb_open_failed", "Não foi possível abrir o dispositivo USB")
        val openedPort = driver.ports.firstOrNull()
        if (openedPort == null) {
            openedConnection.close()
            throw Kw1281Exception("serial_port_missing", "O dispositivo FTDI não expõe uma porta série")
        }
        try {
            openedPort.open(openedConnection)
            openedPort.setParameters(baudrate, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
            openedPort.setFlowControl(UsbSerialPort.FlowControl.NONE)
            connection = openedConnection
            port = openedPort
        } catch (error: Exception) {
            runCatching { openedPort.close() }
            openedConnection.close()
            if (error is Kw1281Exception) throw error
            throw Kw1281Exception("usb_serial_config_failed", error.message ?: "Falha ao configurar a porta série")
        }
    }

    override fun initSlow(address: Int, bitTimeMs: Long) {
        val opened = requirePort()
        require(bitTimeMs > 0) { "Invalid KW1281 bit time" }
        val bits = buildKw1281SlowInitBits(address)
        try {
            ensureOperationAllowed()
            setBreak(opened, false, "prelude")
            Thread.sleep(300)
            ensureOperationAllowed()
            bits.forEachIndexed { index, bit ->
                ensureOperationAllowed()
                setBreak(opened, bit == 0, "bit_$index")
                Thread.sleep(bitTimeMs)
            }
            ensureOperationAllowed()
            opened.purgeHwBuffers(false, true)
        } catch (error: InterruptedException) {
            Thread.currentThread().interrupt()
            throw Kw1281Exception("slow_init_cancelled", "Slow initialization was interrupted")
        } catch (error: IOException) {
            throw Kw1281Exception("slow_init_failed", error.message ?: "FTDI BREAK control failed")
        } finally {
            runCatching { setBreak(opened, false, "finally_deassert") }
        }
    }

    override fun write(data: ByteArray) {
        try {
            ensureOperationAllowed()
            requirePort().write(data, IO_TIMEOUT_MS)
            ensureOperationAllowed()
        } catch (error: IOException) {
            throw Kw1281Exception("usb_write_failed", error.message ?: "USB serial write failed")
        }
    }

    override fun read(size: Int, timeoutMs: Int): ByteArray {
        if (size <= 0) return byteArrayOf()
        try {
            ensureOperationAllowed()
            val opened = requirePort()
            val bytes = readFtdiChunk(size, timeoutMs) { buffer, timeout -> opened.read(buffer, timeout) }
            ensureOperationAllowed()
            return bytes
        } catch (error: IOException) {
            throw Kw1281Exception("usb_read_failed", error.message ?: "USB serial read failed")
        }
    }

    @Synchronized
    override fun close() {
        val currentPort = port
        val currentConnection = connection
        port = null
        connection = null
        runCatching { currentPort?.close() }
        currentConnection?.close()
    }

    private fun requirePort(): UsbSerialPort = port
        ?: throw Kw1281Exception("usb_disconnected", "USB serial port is closed")

    private fun setBreak(opened: UsbSerialPort, asserted: Boolean, phase: String) {
        observeBreak(asserted, phase, "requested")
        try {
            opened.setBreak(asserted)
            observeBreak(asserted, phase, "transport_call_returned")
        } catch (error: Exception) {
            observeBreak(asserted, phase, "failed:${error.message ?: error.javaClass.simpleName}")
            throw error
        }
    }

    private fun observeBreak(asserted: Boolean, phase: String, result: String) {
        breakObserver.report(asserted, phase, result)
    }

    private fun ensureOperationAllowed() {
        if (!operationAllowed()) throw Kw1281Exception("operation_cancelled", "Foreground KW1281 operation was cancelled")
    }

    companion object {
        val SUPPORTED_BAUDRATES = setOf(4800, 9600, 10400)
        private const val IO_TIMEOUT_MS = 1000
    }
}

internal class BreakObserverSlot {
    @Volatile private var observer: ((asserted: Boolean, phase: String, result: String) -> Unit)? = null

    fun set(observer: ((asserted: Boolean, phase: String, result: String) -> Unit)?) {
        this.observer = observer
    }

    fun report(asserted: Boolean, phase: String, result: String) {
        try {
            observer?.invoke(asserted, phase, result)
        } catch (_: Exception) {
            // Diagnostic callbacks must not change serial behavior.
        }
    }
}

private const val FTDI_READ_PACKET_BYTES = 64
