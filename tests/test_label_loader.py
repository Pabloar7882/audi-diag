import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from label_loader import find_matching_label_file, load_label_catalog, get_group_field_labels


class LabelLoaderTests(unittest.TestCase):
    def test_finds_matching_label_file(self):
        repo_root = Path(__file__).resolve().parents[1]
        label_dir = repo_root / "#Labels"
        path = find_matching_label_file("038-906-018-AGR", label_dir)
        self.assertTrue(path is not None)
        self.assertTrue(path.name.lower().endswith(".lbl"))

    def test_loads_measuring_block_labels(self):
        repo_root = Path(__file__).resolve().parents[1]
        label_dir = repo_root / "#Labels"
        catalog = load_label_catalog(label_dir)
        self.assertIn("038-906-018-AGR", catalog)
        labels = get_group_field_labels(3, catalog["038-906-018-AGR"])
        self.assertTrue(any("Engine Speed" in label for label in labels.values()))
        self.assertTrue(any("Air Mass" in label for label in labels.values()))


if __name__ == "__main__":
    unittest.main()
