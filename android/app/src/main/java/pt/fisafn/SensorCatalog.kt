package pt.fisafn

import android.content.res.AssetManager
import org.json.JSONArray
import org.json.JSONObject

internal enum class CatalogStatus {
    ACTIVE_ALLOWED,
    IMPLEMENTED_UNVERIFIED,
    METADATA_ONLY,
    UNKNOWN,
    UNSUPPORTED_CONFIRMED,
}

internal data class CatalogGroup(
    val groupId: Int,
    val name: String,
    val status: CatalogStatus,
    val description: String?,
    val metadataCandidateCount: Int,
    val metadataSourceCount: Int,
) {
    val physicalReadAllowed: Boolean get() = PhysicalGroupPolicy.isAllowed(groupId)
}

internal data class CatalogSensor(
    val sensorId: String,
    val groupId: Int,
    val fieldId: Int?,
    val name: String,
    val unit: String,
    val status: CatalogStatus,
    val decoderId: String?,
    val ecuCandidates: List<String>,
) {
    val liveReadable: Boolean
        get() = physicalReadAllowed && decoderId != null &&
            status in setOf(CatalogStatus.ACTIVE_ALLOWED, CatalogStatus.IMPLEMENTED_UNVERIFIED)

    private val physicalReadAllowed: Boolean get() = PhysicalGroupPolicy.isAllowed(groupId)
}

internal data class SensorCatalog(
    val schemaVersion: Int,
    val groups: List<CatalogGroup>,
    val sensors: List<CatalogSensor>,
) {
    fun group(groupId: Int): CatalogGroup? = groups.firstOrNull { it.groupId == groupId }
    fun sensor(sensorId: String): CatalogSensor? = sensors.firstOrNull { it.sensorId == sensorId }

    /** Metadata never expands the immutable policy list. */
    fun physicalGroups(): List<CatalogGroup> = PhysicalGroupPolicy.allowedGroups.mapNotNull(::group)

    val hasAfnSensorMapping: Boolean
        get() = sensors.any { sensor -> sensor.ecuCandidates.any { it.contains("AFN", ignoreCase = true) } }

    val hasUnconfirmedAfnFieldMappings: Boolean get() = !hasAfnSensorMapping

    companion object {
        fun fromAssets(assets: AssetManager): SensorCatalog =
            assets.open("sensor_catalog.v1.json").bufferedReader(Charsets.UTF_8).use { parse(it.readText()) }

        fun parse(json: String): SensorCatalog {
            val root = JSONObject(json)
            require(root.optInt("schema_version", -1) == 1) { "Unsupported sensor catalog schema" }
            val decoders = parseDecoders(requiredArray(root, "decoders"))
            val groups = parseGroups(requiredArray(root, "groups"))
            val sensors = parseSensors(requiredArray(root, "sensors"), decoders)
            return SensorCatalog(1, groups, sensors)
        }

        private fun requiredArray(root: JSONObject, key: String): JSONArray =
            root.optJSONArray(key) ?: throw IllegalArgumentException("$key must be an array")

        private fun parseGroups(array: JSONArray): List<CatalogGroup> {
            val seen = mutableSetOf<Int>()
            return List(array.length()) { index ->
                val row = array.getJSONObject(index)
                val groupId = row.getInt("group_id")
                require(groupId in 0..255) { "group_id is out of range" }
                require(seen.add(groupId)) { "duplicate group_id $groupId" }
                val status = parseStatus(row.getString("status"))
                require(status != CatalogStatus.ACTIVE_ALLOWED || PhysicalGroupPolicy.isAllowed(groupId)) {
                    "catalog cannot authorize group $groupId"
                }
                require(row.getString("name").isNotBlank()) { "group name is empty" }
                requireSourceRefs(
                    row.optJSONArray("source_refs"),
                    "group source_refs",
                    required = status != CatalogStatus.UNKNOWN,
                )
                val candidateCount = optionalNonnegativeCount(row, "metadata_candidate_count")
                val sourceCount = optionalNonnegativeCount(row, "metadata_source_count")
                val description = row.optString("description").takeIf { it.isNotBlank() }
                CatalogGroup(groupId, row.getString("name"), status, description, candidateCount, sourceCount)
            }
        }

        private fun parseSensors(array: JSONArray, decoderIds: Set<String>): List<CatalogSensor> {
            val seen = mutableSetOf<String>()
            return List(array.length()) { index ->
                val row = array.getJSONObject(index)
                val sensorId = row.getString("sensor_id")
                require(sensorId.isNotBlank() && seen.add(sensorId)) { "empty or duplicate sensor_id" }
                val groupId = row.getInt("group_id")
                require(groupId in 0..255) { "sensor group_id is out of range" }
                val fieldValue = row.opt("field_id")
                val fieldId = if (fieldValue == null || fieldValue == JSONObject.NULL) null else (fieldValue as Number).toInt()
                require(fieldId == null || fieldId in 0..255) { "sensor field_id is out of range" }
                val status = parseStatus(row.getString("status"))
                val decoderId = row.optString("decoder_id").takeIf { it.isNotBlank() && it != "null" }
                require(decoderId == null || decoderId in decoderIds) { "sensor references unknown decoder" }
                require(status !in setOf(CatalogStatus.METADATA_ONLY, CatalogStatus.UNKNOWN, CatalogStatus.UNSUPPORTED_CONFIRMED) || decoderId == null) {
                    "metadata-only or unknown sensors cannot assign a decoder"
                }
                require(status != CatalogStatus.IMPLEMENTED_UNVERIFIED || decoderId != null) {
                    "unverified sensor requires a decoder"
                }
                require(row.getString("name").isNotBlank() && row.getString("unit").isNotBlank()) {
                    "sensor name and unit are required"
                }
                requireSourceRefs(row.optJSONArray("source_refs"), "sensor source_refs")
                val candidates = row.optJSONArray("ecu_candidates")?.toStringList().orEmpty()
                CatalogSensor(sensorId, groupId, fieldId, row.getString("name"), row.getString("unit"), status, decoderId, candidates)
            }
        }

        private fun parseDecoders(array: JSONArray): Set<String> {
            val seen = mutableSetOf<String>()
            repeat(array.length()) { index ->
                val row = array.getJSONObject(index)
                val decoderId = row.getString("decoder_id")
                require(decoderId.isNotBlank() && seen.add(decoderId)) { "empty or duplicate decoder_id" }
                val status = parseStatus(row.getString("status"))
                require(status in setOf(CatalogStatus.ACTIVE_ALLOWED, CatalogStatus.IMPLEMENTED_UNVERIFIED)) {
                    "decoder status is not implemented"
                }
                requireSourceRefs(row.optJSONArray("evidence"), "decoder evidence")
            }
            return seen
        }

        private fun requireSourceRefs(array: JSONArray?, label: String, required: Boolean = true) {
            require(!required || (array != null && array.length() > 0)) { "$label must be non-empty" }
            if (array == null) return
            repeat(array.length()) { index ->
                val ref = array.getJSONObject(index)
                require(ref.getString("file").isNotBlank()) { "$label file is empty" }
                val line = ref.opt("line")
                require(line == null || line == JSONObject.NULL || (line as Number).toInt() >= 1) {
                    "$label line is invalid"
                }
            }
        }

        private fun optionalNonnegativeCount(row: JSONObject, key: String): Int {
            val raw = row.opt(key)
            if (raw == null || raw == JSONObject.NULL) return 0
            require(raw is Number && raw.toLong() >= 0 && raw.toLong() <= Int.MAX_VALUE) { "$key must be a non-negative integer" }
            return raw.toInt()
        }

        private fun JSONArray.toStringList(): List<String> = List(length()) { getString(it) }

        private fun parseStatus(value: String): CatalogStatus =
            runCatching { CatalogStatus.valueOf(value) }
                .getOrElse { throw IllegalArgumentException("invalid catalog status $value") }
    }
}
