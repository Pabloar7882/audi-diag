package pt.fisafn

import android.hardware.usb.UsbManager
import com.hoho.android.usbserial.driver.FtdiSerialDriver
import com.hoho.android.usbserial.driver.UsbSerialProber

data class UsbCandidate(
    val deviceName: String,
    val vendorId: Int,
    val productId: Int,
    val permissionGranted: Boolean,
    val driverName: String,
)

class UsbDeviceScanner(private val manager: UsbManager) {
    fun candidates(): List<UsbCandidate> =
        UsbSerialProber.getDefaultProber().findAllDrivers(manager)
            .filterIsInstance<FtdiSerialDriver>()
            .map { driver ->
                val device = driver.device
                UsbCandidate(
                    deviceName = device.deviceName,
                    vendorId = device.vendorId,
                    productId = device.productId,
                    permissionGranted = manager.hasPermission(device),
                    driverName = driver.javaClass.simpleName,
                )
            }
}
