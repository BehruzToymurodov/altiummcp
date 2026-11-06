"""Unit tests for the save / project disk checks and the safe-button rule
(pure Python, no Altium needed).

Run from the repo root:  python -m unittest server.tests.test_save_checks -v
or from server/:          python -m unittest tests.test_save_checks -v
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import design_tools  # noqa: E402
import sandbox  # noqa: E402


class WrittenSince(unittest.TestCase):
    def test_fresh_file_counts_as_written(self):
        with tempfile.TemporaryDirectory() as d:
            t0 = time.time()
            p = Path(d) / "Board.PcbDoc"
            p.write_bytes(b"x")
            self.assertTrue(design_tools.written_since(str(p), t0))

    def test_old_file_is_not_written(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "Board.PcbDoc"
            p.write_bytes(b"x")
            old = time.time() - 3600
            os.utime(p, (old, old))
            self.assertFalse(design_tools.written_since(str(p), time.time()))

    def test_missing_file_is_not_written(self):
        self.assertFalse(design_tools.written_since(r"Z:\nope\Board.PcbDoc", 0))


class CopiesCreated(unittest.TestCase):
    def test_finds_new_copy_only(self):
        with tempfile.TemporaryDirectory() as d:
            old_copy = Path(d) / "Copy of Old.PcbDoc"
            old_copy.write_bytes(b"x")
            old = time.time() - 3600
            os.utime(old_copy, (old, old))
            t0 = time.time()
            (Path(d) / "Copy of Board.PcbDoc").write_bytes(b"x")
            (Path(d) / "Board.PcbDoc").write_bytes(b"x")
            found = design_tools.copies_created([d], t0)
            self.assertEqual([Path(f).name for f in found], ["Copy of Board.PcbDoc"])

    def test_missing_folder_is_ignored(self):
        self.assertEqual(design_tools.copies_created([r"Z:\nope"], 0), [])


class ClassifySave(unittest.TestCase):
    def _old_file(self, d, name):
        p = Path(d) / name
        p.write_bytes(b"x")
        old = time.time() - 3600
        os.utime(p, (old, old))
        return str(p)

    def test_unattempted_named_file_is_not_written(self):
        # Live bug: Altium skipped both files, the tool said success
        with tempfile.TemporaryDirectory() as d:
            p = self._old_file(d, "Board.PcbDoc")
            r = design_tools.classify_save([{"path": p, "attempted": False}],
                                           time.time(), must_write=True)
            self.assertEqual(r["not_written"], [p])
            self.assertEqual(r["skipped_unmodified"], [])

    def test_unattempted_unforced_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._old_file(d, "Board.PcbDoc")
            r = design_tools.classify_save([{"path": p, "attempted": False}],
                                           time.time(), must_write=False)
            self.assertEqual(r["skipped_unmodified"], [p])
            self.assertEqual(r["not_written"], [])

    def test_fresh_file_is_saved(self):
        with tempfile.TemporaryDirectory() as d:
            t0 = time.time()
            p = Path(d) / "Board.PcbDoc"
            p.write_bytes(b"x")
            r = design_tools.classify_save([{"path": str(p), "attempted": True}],
                                           t0, must_write=True)
            self.assertEqual(r["saved"], [str(p)])

    def test_attempted_but_old_file_is_not_written(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._old_file(d, "Board.PcbDoc")
            r = design_tools.classify_save([{"path": p, "attempted": True}],
                                           time.time(), must_write=False)
            self.assertEqual(r["not_written"], [p])


class ProjectDocumentPaths(unittest.TestCase):
    def test_reads_entries_with_bom_and_duplicates(self):
        text = ("[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=A.SchDoc\r\n"
                "[Document2]\r\nDocumentPath=B.PcbDoc\r\n[Document3]\r\nDocumentPath=A.SchDoc\r\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "X.PrjPcb"
            p.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
            self.assertEqual(design_tools.project_document_paths(str(p)),
                             ["A.SchDoc", "B.PcbDoc", "A.SchDoc"])

    def test_missing_project_gives_empty_list(self):
        self.assertEqual(design_tools.project_document_paths(r"Z:\nope.PrjPcb"), [])


class SafeButtons(unittest.TestCase):
    def test_yes_is_never_a_safe_answer(self):
        self.assertNotIn("yes", [b.lower() for b in sandbox.SAFE_BUTTONS])

    def test_no_is_preferred_over_ok(self):
        order = [b.lower() for b in sandbox.SAFE_BUTTONS]
        self.assertLess(order.index("no"), order.index("ok"))
        self.assertLess(order.index("cancel"), order.index("ok"))


if __name__ == "__main__":
    unittest.main()
