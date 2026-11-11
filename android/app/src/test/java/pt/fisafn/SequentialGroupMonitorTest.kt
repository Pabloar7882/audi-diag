package pt.fisafn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SequentialGroupMonitorTest {
    @Test
    fun monitorIsOptInAndUsesOnlyAuthorizedSequentialGroups() {
        val monitor = SequentialGroupMonitor(minimumPeriodMillis = 1000L)

        assertFalse(monitor.isActive)
        assertNull(monitor.beginRequest())
        val generation = monitor.start()

        val first = monitor.beginRequest()!!
        assertEquals(1, first.groupId)
        assertNull(monitor.beginRequest()) // A second in-flight request is impossible.
        val next = monitor.completeRequest(first)
        assertEquals(3, next?.nextGroupId)
        assertEquals(1000L, next?.delayMillis)

        val second = monitor.beginRequest()!!
        assertEquals(3, second.groupId)
        assertEquals(11, monitor.completeRequest(second)?.nextGroupId)
        val third = monitor.beginRequest()!!
        assertEquals(11, third.groupId)
        assertEquals(1, monitor.completeRequest(third)?.nextGroupId)
        assertEquals(generation, first.generation)
    }

    @Test
    fun stoppingDuringRequestPreventsTheNextGroup() {
        val monitor = SequentialGroupMonitor(minimumPeriodMillis = 500L)
        monitor.start()
        val request = monitor.beginRequest()!!
        assertEquals(1, request.groupId)

        monitor.stop()

        assertFalse(monitor.isActive)
        assertNull(monitor.completeRequest(request))
        assertNull(monitor.beginRequest())
    }

    @Test
    fun stoppedMonitorIgnoresOldCompletionAfterAReplacementStarts() {
        val monitor = SequentialGroupMonitor(minimumPeriodMillis = 1000L)
        monitor.start()
        val oldRequest = monitor.beginRequest()!!

        monitor.stop()
        monitor.start()

        assertNull(monitor.completeRequest(oldRequest))
        assertEquals(1, monitor.beginRequest()?.groupId)
    }

    @Test
    fun aRequestErrorSuspendsTheCycleWithoutIssuingTheNextGroup() {
        val monitor = SequentialGroupMonitor(minimumPeriodMillis = 1000L)
        monitor.start()
        val request = monitor.beginRequest()!!

        assertTrue(monitor.failRequest(request))

        assertFalse(monitor.isActive)
        assertNull(monitor.beginRequest())
        assertNull(monitor.completeRequest(request))
    }

    @Test
    fun monitorRejectsIntervalsOutsideConservativeBounds() {
        assertTrue(runCatching { SequentialGroupMonitor(499L) }.isFailure)
        assertTrue(runCatching { SequentialGroupMonitor(60_001L) }.isFailure)
    }
}
