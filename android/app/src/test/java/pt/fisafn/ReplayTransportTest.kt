package pt.fisafn

import org.junit.Assert.assertEquals
import org.junit.Test

class ReplayTransportTest {
    @Test
    fun healthyReplayProducesFreshReadOnlyValues() {
        val state = ReplayTransport().healthy()
        assertEquals("replay", state.source)
        assertEquals("fresh", state.freshness)
        assertEquals(820.0, state.values.first { it.key == "rpm" }.value!!, 0.0)
    }

    @Test
    fun timeoutReplayClearsValuesAndReportsRecoverableError() {
        val state = ReplayTransport().timeout()
        assertEquals("unavailable", state.freshness)
        assertEquals("timeout", state.error?.code)
        assertEquals(0, state.values.size)
    }
}
