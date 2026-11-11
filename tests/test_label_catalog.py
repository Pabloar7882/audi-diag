import hashlib
import errno
import importlib
import io
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


def load_scan_labels(test_case):
    try:
        module = importlib.import_module("label_catalog")
    except ModuleNotFoundError as error:
        if error.name == "label_catalog":
            test_case.fail("label_catalog.scan_labels is not implemented")
        raise
    if not hasattr(module, "scan_labels"):
        test_case.fail("label_catalog.scan_labels is not implemented")
    return module.scan_labels


class LabelCatalogTest(unittest.TestCase):
    def setUp(self):
        self.scan_labels = load_scan_labels(self)
        self.temp = Path(__file__).resolve().parent / f"_label_catalog_{uuid.uuid4().hex}"
        self.temp.mkdir()
        self.addCleanup(shutil.rmtree, self.temp)
        self.root = self.temp / "Labels"
        self.root.mkdir()

    def write(self, relative_path, contents):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, str):
            contents = contents.encode("utf-8")
        path.write_bytes(contents)
        return path

    def document(self, inventory, relative_path):
        return next(item for item in inventory.files if item.relative_path == relative_path)

    def create_symlink_or_skip(self, link, target):
        try:
            link.symlink_to(target)
        except NotImplementedError as error:
            self.skipTest(f"symlink creation is unavailable: {error}")
        except OSError as error:
            unsupported_errnos = {
                getattr(errno, name)
                for name in ("EACCES", "EPERM", "ENOSYS", "ENOTSUP", "EOPNOTSUPP")
                if hasattr(errno, name)
            }
            if error.errno in unsupported_errnos or getattr(error, "winerror", None) in {1, 5, 50, 1314}:
                self.skipTest(f"symlink creation is unavailable: {error}")
            raise

    def test_keeps_comma_fields_and_trailing_missing_positions(self):
        self.write("fields.lbl", "7,0,Group seven\n7,1,Operating,condition,mode\n7,2,\n")

        inventory = self.scan_labels(self.root)
        rows = self.document(inventory, "fields.lbl").fields

        self.assertEqual([row.raw_fields for row in rows], [
            ("7", "0", "Group seven"),
            ("7", "1", "Operating", "condition", "mode"),
            ("7", "2", ""),
        ])
        self.assertEqual(rows[1].raw_text, "7,1,Operating,condition,mode")
        self.assertEqual(rows[1].source_line, 2)

    def test_accepts_group_zero_and_eleven_variable_fields(self):
        self.write("zero.lbl", "\n".join(f"000,{index},value-{index}" for index in range(11)))

        inventory = self.scan_labels(self.root)
        rows = self.document(inventory, "zero.lbl").fields

        self.assertEqual(len(rows), 11)
        self.assertEqual({row.group_id for row in rows}, {0})
        self.assertEqual([row.field_id for row in rows], list(range(11)))

    def test_ignores_comment_and_blank_lines(self):
        self.write("comments.lbl", "; semi\n# hash\n// slash\n\n12,1,kept\n")

        rows = self.document(self.scan_labels(self.root), "comments.lbl").fields

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source_line, 5)

    def test_bounds_retained_ecu_headers_without_counting_unrelated_comments(self):
        contents = "; ordinary comment\n" * 40
        contents += "; Component: Engine - AGR\n; Component: Engine - ALH\n"
        contents += "; P/N: 038-906-012-???\n"
        self.write("headers.lbl", contents)

        with patch("label_catalog.MAX_HEADER_LINES", 2, create=True):
            inventory = self.scan_labels(self.root)

        document = self.document(inventory, "headers.lbl")
        self.assertEqual(document.component, "Engine - AGR")
        self.assertTrue(any("header metadata limit" in warning.casefold() for warning in inventory.warnings))

    def test_decodes_utf8_bom_and_reports_encoding(self):
        self.write("bom.lbl", b"\xef\xbb\xbf1,1,Caf\xc3\xa9\n")

        document = self.document(self.scan_labels(self.root), "bom.lbl")

        self.assertEqual(document.encoding, "utf-8-sig")
        self.assertEqual(document.fields[0].raw_fields[2], "Café")

    def test_decodes_windows_1252_and_reports_encoding(self):
        self.write("legacy.lbl", b"1,1,Temp\xe9rature\n")

        document = self.document(self.scan_labels(self.root), "legacy.lbl")

        self.assertEqual(document.encoding, "cp1252")
        self.assertEqual(document.fields[0].raw_fields[2], "Température")

    def test_records_replacement_when_cp1252_has_undefined_byte(self):
        self.write("replacement.lbl", b"1,1,bad\x81byte\n")

        inventory = self.scan_labels(self.root)
        document = self.document(inventory, "replacement.lbl")

        self.assertEqual(document.encoding, "cp1252-replace")
        self.assertIn("�", document.fields[0].raw_fields[2])
        self.assertTrue(any("replacement" in warning.casefold() for warning in inventory.warnings))

    def test_preserves_duplicate_fields_and_warns(self):
        self.write("duplicates.lbl", "3,1,first\n3,1,second\n")

        inventory = self.scan_labels(self.root)
        rows = self.document(inventory, "duplicates.lbl").fields

        self.assertEqual([row.raw_fields[2] for row in rows], ["first", "second"])
        self.assertTrue(any("duplicate" in warning.casefold() for warning in inventory.warnings))

    def test_extracts_component_ecu_reference_and_conservative_unit_range(self):
        self.write(
            "ecu.lbl",
            "; Component: Engine (#01) - AGR / ALH\n"
            "; P/N: 038-906-012-???\n"
            "1,1,Engine speed (RPM),Range: 700..900\n",
        )

        document = self.document(self.scan_labels(self.root), "ecu.lbl")

        self.assertEqual(document.component, "Engine (#01) - AGR / ALH")
        self.assertIn("038-906-012-???", document.ecu_references)
        self.assertEqual(document.fields[0].unit, "RPM")
        self.assertEqual(document.fields[0].range_text, "700..900")

    def test_records_relative_path_size_and_sha256(self):
        raw = b"1,1,stable\r\n"
        self.write("stable.LBL", raw)

        document = self.document(self.scan_labels(self.root), "stable.LBL")

        self.assertEqual(document.size_bytes, len(raw))
        self.assertEqual(document.sha256, hashlib.sha256(raw).hexdigest())

    def test_resolves_redirect_chain_and_marks_missing_target(self):
        self.write("start.lbl", "REDIRECT,middle.LBL,first-code\n")
        self.write("middle.LBL", "REDIRECT,leaf.lbl,second-code\n")
        self.write("leaf.lbl", "1,1,leaf\n")
        self.write("missing.lbl", "REDIRECT,absent.lbl,unknown\n")

        inventory = self.scan_labels(self.root)
        chain = self.document(inventory, "start.lbl").redirects[0]
        missing = self.document(inventory, "missing.lbl").redirects[0]

        self.assertEqual(chain.state, "resolved")
        self.assertEqual(chain.resolved_path, "middle.LBL")
        self.assertEqual(chain.chain, ("start.lbl", "middle.LBL", "leaf.lbl"))
        self.assertEqual(missing.state, "missing")

    def test_marks_redirect_chain_ambiguous_when_no_branch_matches_ecu_reference(self):
        self.write("start.lbl", "REDIRECT,index.lbl,expected-code\n")
        self.write(
            "index.lbl",
            "REDIRECT,one.lbl,other-code-1\nREDIRECT,two.lbl,other-code-2\n",
        )
        self.write("one.lbl", "1,1,one\n")
        self.write("two.lbl", "1,1,two\n")

        inventory = self.scan_labels(self.root)
        redirect = self.document(inventory, "start.lbl").redirects[0]

        self.assertEqual(redirect.state, "ambiguous")
        self.assertTrue(any("ambiguous redirect" in warning.casefold() for warning in inventory.warnings))

    def test_marks_clb_redirect_protected_without_loading_it(self):
        self.write("index.lbl", "REDIRECT,protected.CLB,engine-code\n")
        clb = self.root / "protected.CLB"
        clb.write_bytes(b"not text and must not be opened")
        original_open = Path.open
        opened = []

        def guarded_open(path, *args, **kwargs):
            if path.suffix.casefold() == ".clb":
                opened.append(path)
                raise AssertionError("CLB must not be opened")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            inventory = self.scan_labels(self.root)

        redirect = self.document(inventory, "index.lbl").redirects[0]
        self.assertEqual(redirect.state, "protected")
        self.assertEqual(opened, [])
        self.assertNotIn("protected.CLB", [item.relative_path for item in inventory.files])

    def test_rejects_lbl_path_resolving_to_clb_without_opening_it(self):
        clb = self.write("payload.CLB", b"opaque payload")
        alias = self.write("alias.lbl", b"1,1,should not be read\n")
        original_resolve = Path.resolve
        original_open = Path.open
        opened = []

        def resolve_alias(path, *args, **kwargs):
            if path == alias:
                return original_resolve(clb, *args, **kwargs)
            return original_resolve(path, *args, **kwargs)

        def guarded_open(path, *args, **kwargs):
            if path.suffix.casefold() == ".clb":
                opened.append(path)
                return io.BytesIO(b"1,1,synthetic CLB content\n")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve_alias), patch.object(Path, "open", guarded_open):
            inventory = self.scan_labels(self.root)

        self.assertEqual(opened, [])
        self.assertNotIn("alias.lbl", [item.relative_path for item in inventory.files])
        self.assertTrue(any("non-.lbl target" in warning.casefold() for warning in inventory.warnings))

    def test_symlink_to_clb_is_never_opened(self):
        clb = self.root / "payload.CLB"
        clb.write_bytes(b"opaque payload")
        link = self.root / "alias.lbl"
        self.create_symlink_or_skip(link, clb)
        original_open = Path.open
        opened = []

        def guarded_open(path, *args, **kwargs):
            if path.suffix.casefold() == ".clb":
                opened.append(path)
                return io.BytesIO(b"1,1,synthetic CLB content\n")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            inventory = self.scan_labels(self.root)

        self.assertEqual(opened, [])
        self.assertNotIn("alias.lbl", [item.relative_path for item in inventory.files])
        self.assertTrue(any("non-.lbl target" in warning.casefold() for warning in inventory.warnings))

    def test_rejects_traversal_absolute_drive_and_unc_redirects(self):
        self.write(
            "index.lbl",
            "REDIRECT,../outside.lbl,traversal\n"
            "REDIRECT,/outside.lbl,posix-absolute\n"
            "REDIRECT,C:\\outside.lbl,drive-absolute\n"
            "REDIRECT,\\\\server\\share\\outside.lbl,unc-absolute\n",
        )

        redirects = self.document(self.scan_labels(self.root), "index.lbl").redirects

        self.assertEqual([redirect.state for redirect in redirects], ["rejected"] * 4)

    def test_stops_redirect_cycles_and_depth_over_sixteen(self):
        self.write("a.lbl", "REDIRECT,b.lbl,a\n")
        self.write("b.lbl", "REDIRECT,a.lbl,b\n")
        for index in range(18):
            target = f"depth-{index + 1}.lbl" if index < 17 else "leaf.lbl"
            self.write(f"depth-{index}.lbl", f"REDIRECT,{target},depth\n")
        self.write("leaf.lbl", "1,1,leaf\n")

        inventory = self.scan_labels(self.root)

        self.assertEqual(self.document(inventory, "a.lbl").redirects[0].state, "cycle")
        self.assertEqual(self.document(inventory, "depth-0.lbl").redirects[0].state, "depth_limit")

    def test_rejects_symlink_that_escapes_label_root(self):
        outside = self.temp / "outside.lbl"
        outside.write_text("1,1,secret\n", encoding="utf-8")
        link = self.root / "escape.lbl"
        self.create_symlink_or_skip(link, outside)

        inventory = self.scan_labels(self.root)

        self.assertNotIn("secret", [row.raw_text for item in inventory.files for row in item.fields])
        self.assertTrue(any("escape" in warning.casefold() for warning in inventory.warnings))

    def test_skips_file_larger_than_two_mib(self):
        self.write("large.lbl", b"x" * (2 * 1024 * 1024 + 1))

        inventory = self.scan_labels(self.root)
        document = self.document(inventory, "large.lbl")

        self.assertEqual(document.size_bytes, 2 * 1024 * 1024 + 1)
        self.assertIsNone(document.sha256)
        self.assertTrue(any("size" in warning.casefold() for warning in inventory.warnings))

    def test_caps_cumulative_bytes_at_sixteen_mib(self):
        size = 1_900 * 1024
        for index in range(9):
            self.write(f"{index}.lbl", b"1,1,x\n" + b" " * (size - 6))

        inventory = self.scan_labels(self.root)

        self.assertIsNotNone(self.document(inventory, "0.lbl").sha256)
        self.assertIsNone(self.document(inventory, "8.lbl").sha256)
        self.assertTrue(any("total" in warning.casefold() or "byte" in warning.casefold() for warning in inventory.warnings))

    def test_caps_parsed_rows_at_one_hundred_thousand(self):
        self.write("rows.lbl", "\n".join(f"1,{index},x" for index in range(100_001)))

        inventory = self.scan_labels(self.root)

        self.assertEqual(sum(len(item.fields) for item in inventory.files), 100_000)
        self.assertTrue(any("row" in warning.casefold() for warning in inventory.warnings))

    def test_caps_redirect_rows_with_the_global_row_limit(self):
        self.write(
            "redirects.lbl",
            "".join(f"REDIRECT,target-{index}.lbl,code-{index}\n" for index in range(5)),
        )

        with patch("label_catalog.MAX_ROWS", 3):
            inventory = self.scan_labels(self.root)

        self.assertEqual(len(self.document(inventory, "redirects.lbl").redirects), 3)
        self.assertTrue(any("row limit" in warning.casefold() for warning in inventory.warnings))

    def test_caps_file_inventory_deterministically(self):
        self.write("c.lbl", "1,1,c\n")
        self.write("a.lbl", "1,1,a\n")
        self.write("b.lbl", "1,1,b\n")

        with patch("label_catalog.MAX_FILES", 2, create=True):
            first = self.scan_labels(self.root)
            second = self.scan_labels(self.root)

        self.assertEqual([item.relative_path for item in first.files], ["a.lbl", "b.lbl"])
        self.assertEqual([item.relative_path for item in first.files], [item.relative_path for item in second.files])
        self.assertTrue(any("file inventory limit" in warning.casefold() for warning in first.warnings))

    def test_caps_warnings_at_one_thousand(self):
        self.write("warnings.lbl", "\n".join("1,1,duplicate" for _ in range(1_101)))

        inventory = self.scan_labels(self.root)

        self.assertLessEqual(len(inventory.warnings), 1_000)

    def test_output_order_and_hashes_are_stable(self):
        self.write("z.lbl", "1,1,z\n")
        self.write("A.LBL", "1,1,a\n")

        first = self.scan_labels(self.root)
        second = self.scan_labels(self.root)

        self.assertEqual([item.relative_path for item in first.files], ["A.LBL", "z.lbl"])
        self.assertEqual(
            [(item.relative_path, item.sha256) for item in first.files],
            [(item.relative_path, item.sha256) for item in second.files],
        )

    def test_missing_afn_files_are_related_candidates_not_exact_matches(self):
        self.write("028-906-021-AHU.lbl", "; TDi 1.9 (1Z engine) ECU 028 906 021 xxx\n1,1,ah\n")
        self.write("038-906-012-AGR.lbl", "; Component: Engine - AGR / ALH\n1,1,agr\n")
        self.write("038-906-018-AGR.lbl", "; Component: Engine - AGR / ALH\n1,1,agr\n")
        self.write("00-01.lbl", "REDIRECT,077-910-560-BFMS.CLB,unrelated\n")

        inventory = self.scan_labels(self.root)
        targets = {candidate.part_number: candidate for candidate in inventory.afn_targets}

        self.assertEqual(set(targets), {
            "028-906-021-AFN", "038-906-012-AFN", "038-906-018-AFN"
        })
        self.assertEqual([path for target in targets.values() for path in target.exact_matches], [])
        self.assertEqual(targets["028-906-021-AFN"].related_candidates, ("028-906-021-AHU.lbl",))
        self.assertEqual(targets["038-906-012-AFN"].related_candidates, ("038-906-012-AGR.lbl",))
        self.assertEqual(targets["038-906-018-AFN"].related_candidates, ("038-906-018-AGR.lbl",))
        self.assertNotIn(
            "00-01.lbl",
            [path for target in targets.values() for path in target.related_candidates],
        )

    def test_preserves_duplicate_exact_ecu_candidates_and_warns(self):
        self.write("a/028-906-021-AFN.lbl", "1,1,first\n")
        self.write("b/028-906-021-AFN.LBL", "1,1,second\n")

        inventory = self.scan_labels(self.root)
        target = next(item for item in inventory.afn_targets if item.part_number == "028-906-021-AFN")

        self.assertEqual(
            target.exact_matches,
            ("a/028-906-021-AFN.lbl", "b/028-906-021-AFN.LBL"),
        )
        self.assertTrue(any("duplicate exact AFN" in warning for warning in inventory.warnings))

    def test_scanning_labels_does_not_change_physical_measurement_allowlist(self):
        from diagnostic_groups import SUPPORTED_GROUP_IDS

        before = SUPPORTED_GROUP_IDS
        self.write("metadata.lbl", "000,0,Basic group\n001,1,Engine speed\n")

        self.scan_labels(self.root)

        self.assertEqual(SUPPORTED_GROUP_IDS, before)
        self.assertEqual(SUPPORTED_GROUP_IDS, (1, 3, 11))


if __name__ == "__main__":
    unittest.main()
