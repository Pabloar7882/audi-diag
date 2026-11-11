package pt.fisafn

class ReplayTransport {
    fun healthy(): DiagnosticState = DiagnosticState(
        status = "live",
        freshness = "fresh",
        ecu = "038906018 / 038906018AF / Bosch",
        values = listOf(
            DiagnosticValue("rpm", 820.0, "rpm"),
            DiagnosticValue("boost_actual_mbar", 1890.0, "mbar"),
            DiagnosticValue("boost_requested_mbar", 1950.0, "mbar"),
            DiagnosticValue("n75_percent", 62.0, "%"),
        ),
    )

    fun timeout(): DiagnosticState = DiagnosticState(
        status = "error",
        freshness = "unavailable",
        error = DiagnosticErrorState("timeout", "ECU não respondeu", recoverable = true),
    )

    fun disconnected(): DiagnosticState = DiagnosticState(
        status = "error",
        freshness = "unavailable",
        error = DiagnosticErrorState("disconnected", "Adaptador USB desligado", recoverable = true),
    )
}
