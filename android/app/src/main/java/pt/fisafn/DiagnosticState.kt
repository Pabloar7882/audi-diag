package pt.fisafn

data class DiagnosticValue(
    val key: String,
    val value: Double?,
    val unit: String,
    val freshness: String = "fresh",
    val source: String = "replay",
    val raw: String? = null,
)

data class DiagnosticErrorState(
    val code: String,
    val message: String,
    val recoverable: Boolean = false,
)

data class DiagnosticState(
    val mode: String = "replay",
    val status: String = "idle",
    val source: String = "replay",
    val freshness: String = "unavailable",
    val ecu: String? = null,
    val ecuComponent: String? = null,
    val ecuSoftwareVersion: String? = null,
    val ecuExtraIdentification: String? = null,
    val values: List<DiagnosticValue> = emptyList(),
    val history: List<DiagnosticValue> = emptyList(),
    val error: DiagnosticErrorState? = null,
)
