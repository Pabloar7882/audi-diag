package pt.fisafn

import org.junit.Assert.assertEquals
import org.junit.Test

class BreakObserverSlotTest {
    @Test
    fun reportsOnlyWhileAnObserverIsInstalled() {
        val events = mutableListOf<String>()
        val slot = BreakObserverSlot()

        slot.set { asserted, phase, result -> events += "$asserted:$phase:$result" }
        slot.report(false, "prelude", "requested")
        slot.set(null)
        slot.report(true, "bit_0", "transport_call_returned")

        assertEquals(listOf("false:prelude:requested"), events)
    }
}
