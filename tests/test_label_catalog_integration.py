import unittest
from pathlib import Path

from label_catalog import scan_labels
from label_catalog_integration import LabelMetadataCatalog, LabelQueryError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "label_integration"


class LabelCatalogIntegrationTests(unittest.TestCase):
    def catalog(self):
        return LabelMetadataCatalog.from_inventory(scan_labels(FIXTURE_ROOT))

    def test_candidates_keep_line_provenance_and_never_gain_decoder_or_live_access(self):
        catalog = self.catalog()
        result = catalog.query(query="G28")

        self.assertEqual(result["total"], 1)
        sensor = result["items"][0]
        self.assertEqual(sensor["group_id"], 1)
        self.assertEqual(sensor["field_id"], 1)
        self.assertEqual(sensor["status"], "METADATA_ONLY")
        self.assertIsNone(sensor["decoder_id"])
        self.assertFalse(sensor["live_readable"])
        self.assertEqual(sensor["source_file"], "engine.lbl")
        self.assertEqual(sensor["source_line"], 3)
        self.assertEqual(sensor["unit"].casefold(), "rpm")
        self.assertEqual(sensor["ecu_candidates"], ["AGR"])

    def test_duplicates_are_preserved_as_distinct_candidates(self):
        result = self.catalog().query(group_id=2)
        duplicates = [item for item in result["items"] if item["field_id"] == 1]

        self.assertEqual(result["total"], 2)
        self.assertEqual(len(duplicates), 2)
        self.assertNotEqual(duplicates[0]["sensor_id"], duplicates[1]["sensor_id"])

    def test_conflicting_label_candidates_are_reported_without_selecting_a_winner(self):
        conflicts = self.catalog().conflicts()

        self.assertEqual(len(conflicts), 1)
        self.assertEqual((conflicts[0]["group_id"], conflicts[0]["field_id"]), (2, 1))
        self.assertEqual(conflicts[0]["candidate_count"], 2)
        self.assertEqual(conflicts[0]["variant_count"], 2)

    def test_groups_separate_physical_permission_from_label_metadata(self):
        groups = {item["id"]: item for item in self.catalog().groups()}

        self.assertEqual(groups[1]["status"], "ACTIVE_ALLOWED")
        self.assertTrue(groups[1]["physical_read_allowed"])
        self.assertEqual(groups[1]["metadata_candidate_count"], 2)
        self.assertEqual(groups[2]["status"], "METADATA_ONLY")
        self.assertFalse(groups[2]["physical_read_allowed"])
        self.assertEqual(groups[4]["status"], "UNKNOWN")
        self.assertFalse(groups[4]["physical_read_allowed"])
        self.assertEqual(len(groups), 256)

    def test_query_filters_group_and_paginates_deterministically(self):
        catalog = self.catalog()
        first = catalog.query(group_id=2, page=1, page_size=1)
        second = catalog.query(group_id=2, page=2, page_size=1)

        self.assertEqual(first["total"], 2)
        self.assertEqual(first["page"], 1)
        self.assertEqual(first["page_size"], 1)
        self.assertEqual(first["items"][0]["source_line"], 4)
        self.assertEqual(second["items"][0]["source_line"], 5)

    def test_group_status_filter_does_not_relabel_metadata_rows_as_decoded(self):
        catalog = self.catalog()
        active = catalog.query(group_status="ACTIVE_ALLOWED")
        metadata = catalog.query(group_status="METADATA_ONLY")
        unknown = catalog.query(group_status="UNKNOWN")

        self.assertEqual(active["total"], 2)
        self.assertEqual(metadata["total"], 2)
        self.assertEqual(unknown["total"], 0)
        self.assertTrue(all(item["status"] == "METADATA_ONLY" for item in active["items"]))

    def test_query_bounds_and_rejects_invalid_filters(self):
        catalog = self.catalog()
        with self.assertRaises(LabelQueryError):
            catalog.query(query="x" * 129)
        with self.assertRaises(LabelQueryError):
            catalog.query(group_id=256)
        with self.assertRaises(LabelQueryError):
            catalog.query(page=0)
        with self.assertRaises(LabelQueryError):
            catalog.query(page_size=101)


if __name__ == "__main__":
    unittest.main()
