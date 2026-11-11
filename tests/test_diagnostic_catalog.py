import json
import unittest
from pathlib import Path

from diagnostic_catalog import (
    CatalogError,
    CatalogStatus,
    load_catalog,
    merge_label_candidates,
)
from diagnostic_groups import SUPPORTED_GROUP_IDS


ROOT = Path(__file__).resolve().parents[1]


class DiagnosticCatalogTests(unittest.TestCase):
    def test_production_catalog_has_only_the_three_enabled_physical_groups(self):
        catalog = load_catalog(ROOT / "data" / "sensor_catalog.v1.json")

        self.assertEqual(catalog.physical_group_ids, (1, 3, 11))
        self.assertEqual(catalog.physical_group_ids, tuple(SUPPORTED_GROUP_IDS))
        self.assertEqual(catalog.status_for_group(1), CatalogStatus.ACTIVE_ALLOWED)
        self.assertIsNone(catalog.sensor("afn.g001.field001"))

    def test_canonical_group_catalog_represents_all_ids_without_expanding_policy(self):
        catalog = load_catalog(ROOT / "data" / "sensor_catalog.v1.json")

        self.assertEqual(len(catalog.groups), 256)
        self.assertEqual(catalog.status_for_group(2), CatalogStatus.METADATA_ONLY)
        self.assertGreater(next(group for group in catalog.groups if group.group_id == 2).metadata_candidate_count, 0)
        self.assertEqual(catalog.status_for_group(255), CatalogStatus.UNKNOWN)
        self.assertEqual(catalog.physical_group_ids, (1, 3, 11))

    def test_metadata_candidate_retains_source_and_never_becomes_live_readable(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        candidate = fixture["sensors"][0]
        catalog = load_catalog(ROOT / "data" / "sensor_catalog.v1.json")

        merged = merge_label_candidates(catalog, [candidate])
        sensor = merged.sensor("afn.group001.coolant_candidate")

        self.assertIsNotNone(sensor)
        self.assertEqual(sensor.status, CatalogStatus.METADATA_ONLY)
        self.assertEqual(sensor.source_refs[0].file, "028-906-021-AFN.LBL")
        self.assertEqual(sensor.source_refs[0].line, 12)
        self.assertEqual(sensor.ecu_candidates, ("028-906-021-AFN",))
        self.assertEqual((sensor.range_min, sensor.range_max), (-40.0, 120.0))
        self.assertEqual(sensor.range_text, "-40..120 °C")
        self.assertFalse(sensor.live_readable)

    def test_unknown_decoder_is_rejected_instead_of_silently_accepted(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        fixture["decoders"] = []
        fixture["sensors"][0]["status"] = "IMPLEMENTED_UNVERIFIED"
        fixture["sensors"][0]["decoder_id"] = "kw1281.unknown"

        with self.assertRaisesRegex(CatalogError, "unknown decoder"):
            load_catalog(fixture)

    def test_unverified_decoder_requires_source_evidence_and_remains_unverified(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        fixture["decoders"] = [{
            "decoder_id": "kw1281.example",
            "status": "IMPLEMENTED_UNVERIFIED",
            "evidence": [{"file": "upstream/decoder.c", "line": 20}],
        }]
        fixture["sensors"][0]["status"] = "IMPLEMENTED_UNVERIFIED"
        fixture["sensors"][0]["decoder_id"] = "kw1281.example"

        catalog = load_catalog(fixture)
        sensor = catalog.sensor("afn.group001.coolant_candidate")

        self.assertEqual(sensor.status, CatalogStatus.IMPLEMENTED_UNVERIFIED)
        self.assertEqual(sensor.decoder_id, "kw1281.example")
        self.assertTrue(sensor.live_readable)
        self.assertEqual(catalog.decoders[0].status, CatalogStatus.IMPLEMENTED_UNVERIFIED)

    def test_unverified_sensor_without_decoder_is_rejected(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        fixture["sensors"][0]["status"] = "IMPLEMENTED_UNVERIFIED"

        with self.assertRaisesRegex(CatalogError, "requires a decoder"):
            load_catalog(fixture)

    def test_status_and_field_schema_are_validated(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        fixture["sensors"][0]["status"] = "CONFIRMED_BY_MAGIC"

        with self.assertRaisesRegex(CatalogError, "status"):
            load_catalog(fixture)

    def test_raw_only_sensor_has_no_decoder(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        sensor = fixture["sensors"][0]
        sensor["status"] = "UNKNOWN"
        sensor["sensor_id"] = "kw1281.group001.raw"
        sensor["field_id"] = None
        sensor["name"] = "Raw group payload"
        sensor["unit"] = "raw"

        catalog = load_catalog(fixture)
        raw = catalog.sensor("kw1281.group001.raw")

        self.assertIsNone(raw.decoder_id)
        self.assertFalse(raw.live_readable)

    def test_active_allowed_raw_only_sensor_is_not_a_decoded_live_value(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        sensor_data = fixture["sensors"][0]
        sensor_data["status"] = "ACTIVE_ALLOWED"
        sensor_data["decoder_id"] = None
        sensor_data["name"] = "Raw response only"
        sensor_data["unit"] = "raw"

        catalog = load_catalog(fixture)
        sensor = catalog.sensor("afn.group001.coolant_candidate")

        self.assertTrue(catalog.groups[0].physical_read_allowed)
        self.assertFalse(sensor.live_readable)

    def test_catalog_cannot_mark_other_group_active(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        fixture["groups"][0]["group_id"] = 2

        with self.assertRaisesRegex(CatalogError, "cannot be ACTIVE_ALLOWED"):
            load_catalog(fixture)

    def test_duplicate_sensor_ids_are_rejected(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        fixture["sensors"].append(dict(fixture["sensors"][0]))

        with self.assertRaisesRegex(CatalogError, "duplicate sensor_id"):
            load_catalog(fixture)

    def test_invalid_range_is_rejected(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        fixture["sensors"][0]["range"] = {"min": 100, "max": -100}

        with self.assertRaisesRegex(CatalogError, "cannot exceed"):
            load_catalog(fixture)

    def test_metadata_catalog_does_not_change_physical_allowlist(self):
        before = tuple(SUPPORTED_GROUP_IDS)
        catalog = load_catalog(ROOT / "data" / "sensor_catalog.v1.json")
        candidate = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))["sensors"][0]
        merge_label_candidates(catalog, [candidate])

        self.assertEqual(tuple(SUPPORTED_GROUP_IDS), before)
        self.assertEqual(before, (1, 3, 11))

    def test_imported_candidate_cannot_claim_live_eligibility(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "sensor_catalog_contract.json").read_text(encoding="utf-8"))
        candidate = fixture["sensors"][0]
        candidate["status"] = "ACTIVE_ALLOWED"
        candidate["physical_read_allowed"] = True
        catalog = load_catalog(ROOT / "data" / "sensor_catalog.v1.json")

        with self.assertRaisesRegex(CatalogError, "label candidate"):
            merge_label_candidates(catalog, [candidate])


if __name__ == "__main__":
    unittest.main()
