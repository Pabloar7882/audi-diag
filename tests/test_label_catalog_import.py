import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from label_catalog import scan_labels
from label_catalog_import import (
    ImportHistoryError,
    JsonImportHistoryStore,
    LabelCatalogImporter,
    build_import_history,
)
from label_catalog_integration import LabelMetadataCatalog
from server import Engine, FISServer


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "label_integration"


class MemoryHistoryStore:
    def __init__(self):
        self.state = {"schema_version": 1, "files": {}, "events": []}

    def load(self):
        return self.state

    def save(self, state):
        self.state = state


class LabelCatalogImportTests(unittest.TestCase):
    def test_initial_import_records_inventory_without_copying_label_text(self):
        inventory = scan_labels(FIXTURE_ROOT)
        catalog = LabelMetadataCatalog.from_inventory(inventory)

        updated, report = build_import_history(
            inventory,
            catalog,
            imported_at="2026-11-10T12:00:00+00:00",
        )

        self.assertEqual(report["added_count"], 1)
        self.assertEqual(report["changed_count"], 0)
        self.assertEqual(report["removed_count"], 0)
        self.assertEqual(report["metadata_rows"], 4)
        self.assertEqual(report["group_count"], 2)
        self.assertTrue(report["history_event_recorded"])
        self.assertEqual(updated["events"][0]["added"], ["engine.lbl"])
        serialized = json.dumps(updated)
        self.assertNotIn("Engine Speed (G28)", serialized)
        self.assertIn(inventory.files[0].sha256, serialized)

    def test_second_identical_import_does_not_add_a_duplicate_history_event(self):
        inventory = scan_labels(FIXTURE_ROOT)
        catalog = LabelMetadataCatalog.from_inventory(inventory)
        first, _ = build_import_history(inventory, catalog, imported_at="first")

        second, report = build_import_history(inventory, catalog, first, imported_at="second")

        self.assertEqual(len(second["events"]), 1)
        self.assertFalse(report["history_event_recorded"])
        self.assertEqual(report["unchanged_count"], 1)

    def test_changed_and_removed_sources_are_reported_but_policy_stays_independent(self):
        inventory = scan_labels(FIXTURE_ROOT)
        catalog = LabelMetadataCatalog.from_inventory(inventory)
        previous = {
            "schema_version": 1,
            "files": {
                "engine.lbl": {"size_bytes": 1, "sha256": "0" * 64, "loaded": True, "format": "utf-8"},
                "old.lbl": {"size_bytes": 1, "sha256": "1" * 64, "loaded": True, "format": "utf-8"},
            },
            "events": [],
        }

        updated, report = build_import_history(inventory, catalog, previous, imported_at="changed")

        self.assertEqual(report["changed_count"], 1)
        self.assertEqual(report["removed_count"], 1)
        self.assertEqual(report["changed_sample"], ["engine.lbl"])
        self.assertEqual(report["removed_sample"], ["old.lbl"])
        self.assertEqual([g for g in catalog.groups() if g["physical_read_allowed"]], [
            g for g in catalog.groups() if g["id"] in (1, 3, 11)
        ])
        self.assertEqual(updated["events"][-1]["changed_count"], 1)

    def test_history_store_round_trips_and_rejects_oversized_files(self):
        path = ROOT / "tests" / "_label_import_history_test.json"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        store = JsonImportHistoryStore(path)
        state = {"schema_version": 1, "files": {}, "events": [{"added_count": 1}]}

        store.save(state)

        self.assertEqual(store.load(), state)
        path.write_text(" " * (8 * 1024 * 1024 + 1), encoding="utf-8")
        with self.assertRaises(ImportHistoryError):
            store.load()

    def test_importer_scans_and_persists_explicitly(self):
        store = MemoryHistoryStore()
        importer = LabelCatalogImporter(FIXTURE_ROOT, store)

        catalog, report = importer.import_now()

        self.assertEqual(catalog.candidate_count, 4)
        self.assertEqual(report["added_count"], 1)
        self.assertIn("engine.lbl", store.state["files"])

    def test_http_import_is_authorized_and_returns_change_report(self):
        history_path = ROOT / "tests" / "_label_import_api_history_test.json"
        self.addCleanup(lambda: history_path.unlink(missing_ok=True))
        engine = Engine(labels_root=FIXTURE_ROOT, label_history_path=history_path)
        server = FISServer(("127.0.0.1", 0), engine, "secret")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            unauthorized = Request(
                f"http://127.0.0.1:{server.server_port}/api/diagnostic/catalog/import",
                data=b"{}",
                method="POST",
            )
            with self.assertRaises(HTTPError) as error:
                urlopen(unauthorized, timeout=3)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/diagnostic/catalog/import?k=secret",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            payload = json.loads(urlopen(request, timeout=3).read())
            self.assertEqual(payload["report"]["added_count"], 1)
            self.assertEqual(payload["report"]["metadata_rows"], 4)
            self.assertTrue(history_path.is_file())
            self.assertEqual(json.loads(history_path.read_text(encoding="utf-8"))["schema_version"], 1)
        finally:
            server.shutdown()
            server.server_close()
            engine.stop.set()


if __name__ == "__main__":
    unittest.main()
