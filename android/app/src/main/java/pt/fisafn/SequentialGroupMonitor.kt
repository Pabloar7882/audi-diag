package pt.fisafn

internal data class MonitorRequest(val generation: Long, val groupId: Int)
internal data class MonitorAdvance(val nextGroupId: Int, val delayMillis: Long)

/** One in-flight request at a time; the caller schedules the next request only after completion. */
internal class SequentialGroupMonitor(val minimumPeriodMillis: Long) {
    private var generation = 0L
    private var active = false
    private var inFlight: MonitorRequest? = null
    private var cursor = 0

    init {
        require(minimumPeriodMillis in MIN_PERIOD_MS..MAX_PERIOD_MS) {
            "monitor interval must be between $MIN_PERIOD_MS and $MAX_PERIOD_MS milliseconds"
        }
    }

    @Synchronized
    fun start(): Long {
        check(!active) { "monitor is already active" }
        generation++
        active = true
        cursor = 0
        inFlight = null
        return generation
    }

    @Synchronized
    fun beginRequest(): MonitorRequest? {
        if (!active || inFlight != null) return null
        return MonitorRequest(generation, PhysicalGroupPolicy.allowedGroups[cursor]).also { inFlight = it }
    }

    @Synchronized
    fun completeRequest(request: MonitorRequest): MonitorAdvance? {
        if (!active || request.generation != generation || inFlight != request) return null
        inFlight = null
        cursor = (cursor + 1) % PhysicalGroupPolicy.allowedGroups.size
        return MonitorAdvance(PhysicalGroupPolicy.allowedGroups[cursor], minimumPeriodMillis)
    }

    @Synchronized
    fun failRequest(request: MonitorRequest): Boolean {
        if (!active || request.generation != generation || inFlight != request) return false
        inFlight = null
        active = false
        generation++
        return true
    }

    @Synchronized
    fun stop() {
        generation++
        active = false
        inFlight = null
    }

    @get:Synchronized
    val isActive: Boolean get() = active

    companion object {
        const val MIN_PERIOD_MS = 500L
        const val MAX_PERIOD_MS = 60_000L
    }
}
