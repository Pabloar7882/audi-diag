package pt.fisafn

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SensorCatalogTest {
    @Test
    fun canonicalCatalogLoadsOfflineAndKeepsPhysicalGroupsBounded() {
        val json = javaClass.classLoader!!.getResource("sensor_catalog.v1.json")!!
            .readBytes().toString(Charsets.UTF_8)

        val catalog = SensorCatalog.parse(json)

        assertEquals(1, catalog.schemaVersion)
        assertEquals(listOf(1, 3, 11), catalog.physicalGroups().map { it.groupId })
        assertEquals(256, catalog.groups.size)
        assertEquals(CatalogStatus.METADATA_ONLY, catalog.group(2)!!.status)
        assertFalse(catalog.group(2)!!.physicalReadAllowed)
        assertTrue(catalog.group(2)!!.metadataCandidateCount > 0)
        assertEquals(CatalogStatus.UNKNOWN, catalog.group(255)!!.status)
        assertTrue(catalog.sensors.isEmpty())
        assertTrue(catalog.hasUnconfirmedAfnFieldMappings)
    }

    @Test
    fun catalogMetadataCannotExpandPhysicalAllowList() {
        val catalog = SensorCatalog.parse(catalogJson(
            groups = """
                {"group_id":1,"name":"Motor","status":"ACTIVE_ALLOWED","source_refs":[{"file":"catalog.json"}]},
                {"group_id":2,"name":"Metadata group","status":"METADATA_ONLY","source_refs":[{"file":"labels/example.lbl","line":8}]},
                {"group_id":4,"name":"Unverified group","status":"IMPLEMENTED_UNVERIFIED","source_refs":[{"file":"doc.md","line":4}]}
            """.trimIndent(),
        ))

        assertEquals(listOf(1), catalog.physicalGroups().map { it.groupId })
        assertFalse(catalog.group(2)!!.physicalReadAllowed)
        assertFalse(catalog.group(4)!!.physicalReadAllowed)
    }

    @Test
    fun allowedGroupDoesNotMakeRawOnlyOrMetadataSensorLiveReadable() {
        val catalog = SensorCatalog.parse(catalogJson(
            groups = "{\"group_id\":1,\"name\":\"Motor\",\"status\":\"ACTIVE_ALLOWED\",\"source_refs\":[{\"file\":\"catalog.json\"}]}",
            sensors = """
                {"sensor_id":"kw.group001.raw","group_id":1,"field_id":null,"name":"Raw response","unit":"raw","status":"ACTIVE_ALLOWED","decoder_id":null,"source_refs":[{"file":"kw1281"}]},
                {"sensor_id":"label.candidate","group_id":1,"field_id":1,"name":"Engine speed candidate","unit":"rpm","status":"METADATA_ONLY","decoder_id":null,"source_refs":[{"file":"labels/example.lbl","line":10}]}
            """.trimIndent(),
        ))

        assertFalse(catalog.sensor("kw.group001.raw")!!.liveReadable)
        assertFalse(catalog.sensor("label.candidate")!!.liveReadable)
    }

    @Test
    fun unknownDecoderIsRejectedAndStatusAloneDoesNotAuthorizeAValue() {
        val error = runCatching {
            SensorCatalog.parse(catalogJson(
                groups = "{\"group_id\":1,\"name\":\"Motor\",\"status\":\"ACTIVE_ALLOWED\",\"source_refs\":[{\"file\":\"catalog.json\"}]}",
                decoders = "",
                sensors = "{\"sensor_id\":\"candidate\",\"group_id\":1,\"field_id\":1,\"name\":\"RPM\",\"unit\":\"rpm\",\"status\":\"IMPLEMENTED_UNVERIFIED\",\"decoder_id\":\"kw.unknown\",\"source_refs\":[{\"file\":\"doc.md\",\"line\":1}]}"
            ))
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun activeAllowedStatusOutsideFixedPolicyIsRejected() {
        val error = runCatching {
            SensorCatalog.parse(catalogJson(
                groups = "{\"group_id\":2,\"name\":\"Future\",\"status\":\"ACTIVE_ALLOWED\",\"source_refs\":[{\"file\":\"catalog.json\"}]}",
            ))
        }.exceptionOrNull()

        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun missingRequiredCatalogArraysAreRejected() {
        val valid = JSONObject(catalogJson(
            groups = "{\"group_id\":1,\"name\":\"Motor\",\"status\":\"ACTIVE_ALLOWED\",\"source_refs\":[{\"file\":\"catalog.json\"}]}",
        ))

        listOf("groups", "sensors", "decoders").forEach { key ->
            val malformed = JSONObject(valid.toString()).apply { remove(key) }
            assertTrue("missing $key must fail", runCatching { SensorCatalog.parse(malformed.toString()) }.isFailure)
        }
    }

    private fun catalogJson(
        groups: String,
        sensors: String = "",
        decoders: String = "",
    ): String = """
        {
          "schema_version":1,
          "groups":[$groups],
          "sensors":[$sensors],
          "decoders":[$decoders]
        }
    """.trimIndent()
}
