package pt.fisafn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DiagnosticSessionCallbacksTest {
    @Test
    fun operationTokenKeepsItsTraceSessionIdWhenANewerOperationStarts() {
        val callbacks = DiagnosticSessionCallbacks()
        val queued = callbacks.begin("usb-a", "trace-a")
        val newer = callbacks.begin("usb-b", "trace-b")

        assertEquals("trace-a", queued.traceSessionId)
        assertEquals("trace-b", newer.traceSessionId)
    }

    @Test
    fun staleConnectSuccessCannotRestoreIdentityOrEnableReadsAfterDetach() {
        val callbacks = DiagnosticSessionCallbacks()
        val old = callbacks.begin("usb-a")
        var identity: String? = null
        var readsEnabled = false
        callbacks.clear()

        callbacks.runIfCurrent(old) {
            identity = "038906018AF"
            readsEnabled = true
        }

        assertEquals(null, identity)
        assertFalse(readsEnabled)
    }

    @Test
    fun staleReadCompletionCannotReplaceDataAfterSameDeviceReconnect() {
        val callbacks = DiagnosticSessionCallbacks()
        val old = callbacks.begin("usb-a")
        callbacks.clear()
        val replacement = callbacks.begin("usb-a")
        var sample = "replacement"

        callbacks.runIfCurrent(old) { sample = "old bytes" }

        assertEquals("replacement", sample)
        assertTrue(callbacks.isCurrent(replacement))
        callbacks.runIfCurrent(replacement) { sample = "new bytes" }
        assertEquals("new bytes", sample)
    }

    @Test
    fun staleKeepAliveFailureCannotCloseReplacementSession() {
        val callbacks = DiagnosticSessionCallbacks()
        val old = callbacks.begin("usb-a")
        callbacks.clear()
        val replacement = callbacks.begin("usb-b")
        var replacementClosed = false

        callbacks.runIfCurrent(old) {
            replacementClosed = true
            callbacks.clear()
        }

        assertFalse(replacementClosed)
        assertTrue(callbacks.isCurrent(replacement))
    }

    @Test
    fun wrongDeviceAndPreviousGenerationFailuresAreIgnored() {
        val callbacks = DiagnosticSessionCallbacks()
        val old = callbacks.begin("usb-a")
        val current = callbacks.begin("usb-a")
        var failures = 0

        callbacks.runIfCurrent(old) { failures++ }
        callbacks.runIfCurrent(current.copy(deviceName = "usb-b")) { failures++ }
        assertEquals(0, failures)

        callbacks.runIfCurrent(current) { failures++ }
        assertEquals(1, failures)
        callbacks.clear()
        assertEquals(null, callbacks.current())
        assertFalse(callbacks.isCurrent(current))
    }
}
