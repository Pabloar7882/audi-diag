import unittest

from diagnostic_groups import default_afn_groups, decode_group
from diagnostic_models import Freshness, SourceKind


class DiagnosticGroupsTest(unittest.TestCase):
    def test_default_groups_include_afn_targets_with_provisional_metadata(self):
        groups = {group.group_id: group for group in default_afn_groups()}
        self.assertTrue({1, 3, 11}.issubset(groups))
        self.assertTrue(all(group.provisional for group in groups.values()))
        self.assertEqual(groups[3].signals[0].key, "maf_actual_mg")

    def test_default_catalog_covers_every_kw1281_group_without_claiming_scales(self):
        groups = default_afn_groups()
        self.assertEqual(len(groups), 256)
        self.assertEqual([group.group_id for group in groups], list(range(256)))
        generic = groups[42]
        self.assertEqual(generic.label, "Grupo 042")
        self.assertEqual(generic.signals, ())
        self.assertTrue(generic.provisional)

    def test_decode_group_preserves_raw_payload_and_unknown_fields(self):
        values = decode_group(
            3,
            b"GROUP|003|rpm=820|maf_actual_mg=410|vendor_raw=7",
        )
        self.assertEqual([value.key for value in values], [
            "rpm", "maf_actual_mg", "vendor_raw",
        ])
        self.assertEqual(values[-1].raw, "vendor_raw=7")
        self.assertEqual(values[0].source, SourceKind.REPLAY)
        self.assertEqual(values[0].freshness, Freshness.FRESH)

    def test_decode_group_rejects_mismatched_group(self):
        with self.assertRaises(ValueError):
            decode_group(11, b"GROUP|003|n75_percent=40")


if __name__ == "__main__":
    unittest.main()
