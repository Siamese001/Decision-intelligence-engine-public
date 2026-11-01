"""
Unit tests for PR Overlap Core: models, hunks, symbols, duplicates, cache, and engine.

SSOT: urn:nee:pr-overlap-evidence:1.0.0
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.pr_overlap_core import (
    BranchEvidence,
    CommitPatchEvidence,
    EvidenceCache,
    FileHunkDiff,
    HunkRange,
    OverlapVerdict,
    compare_branches,
    compare_file_hunks,
    compute_normalized_hunk_hash,
    detect_duplicate_blobs,
    detect_duplicate_commits,
    extract_python_symbols,
    find_touched_symbols,
    is_python_file,
    parse_unified_diff_u0,
    ranges_overlap,
)


class TestPRHunkLogic(unittest.TestCase):
    """Tests hunk parsing, coordinates, normalization, and overlap detection."""

    def test_ranges_overlap_cases(self):
        # Overlapping ranges
        self.assertTrue(ranges_overlap(10, 5, 12, 3))   # [10, 14] and [12, 14]
        self.assertTrue(ranges_overlap(10, 5, 14, 5))   # [10, 14] and [14, 18] (touching at 14)
        self.assertTrue(ranges_overlap(10, 1, 10, 1))   # Identical single line [10, 10]

        # Disjoint ranges
        self.assertFalse(ranges_overlap(10, 5, 20, 5))  # [10, 14] and [20, 24]
        self.assertFalse(ranges_overlap(10, 5, 16, 5))  # [10, 14] and [16, 20]

        # Insertion points (lines == 0)
        self.assertTrue(ranges_overlap(10, 0, 10, 0))   # Both insert at 10
        self.assertFalse(ranges_overlap(10, 0, 15, 0))  # Different insertion points

    def test_parse_unified_diff_u0(self):
        sample_diff = """diff --git a/src/module.py b/src/module.py
index abcdef1..1234567 100644
--- a/src/module.py
+++ b/src/module.py
@@ -10,3 +10,4 @@ def calculate():
-    val = 1
+    val = 2
+    val += 1
@@ -50,1 +51,2 @@ def helper():
-    return True
+    return False
+    # end
diff --git a/docs/readme.md b/docs/readme.md
new file mode 100644
index 0000000..9999999
--- /dev/null
+++ b/docs/readme.md
@@ -0,0 +1,5 @@
+# Readme
+Some docs
"""
        parsed = parse_unified_diff_u0(sample_diff)
        self.assertIn("src/module.py", parsed)
        self.assertIn("docs/readme.md", parsed)

        mod = parsed["src/module.py"]
        self.assertEqual(mod.status, "M")
        self.assertEqual(len(mod.hunks), 2)
        self.assertEqual(mod.hunks[0].old_start, 10)
        self.assertEqual(mod.hunks[0].old_lines, 3)
        self.assertEqual(mod.hunks[0].new_start, 10)
        self.assertEqual(mod.hunks[0].new_lines, 4)

        readme = parsed["docs/readme.md"]
        self.assertEqual(readme.status, "A")
        self.assertEqual(len(readme.hunks), 1)

    def test_compare_file_hunks_disjoint_and_overlapping(self):
        h1 = [HunkRange(old_start=10, old_lines=5, new_start=10, new_lines=6)]
        h2_disjoint = [HunkRange(old_start=50, old_lines=5, new_start=51, new_lines=5)]
        h3_overlapping = [HunkRange(old_start=12, old_lines=4, new_start=12, new_lines=4)]

        is_disjoint, ov = compare_file_hunks(h1, h2_disjoint)
        self.assertTrue(is_disjoint)
        self.assertEqual(len(ov), 0)

        is_disjoint_ov, ov2 = compare_file_hunks(h1, h3_overlapping)
        self.assertFalse(is_disjoint_ov)
        self.assertEqual(len(ov2), 1)

    def test_compute_normalized_hunk_hash(self):
        # Whitespace differences should yield identical normalized hash
        hunk_a = ["+    x = 10  ", "+    y = 20  "]
        hunk_b = ["+ x = 10", "+ y = 20"]
        hunk_c = ["+    x = 99", "+    y = 20"]

        hash_a = compute_normalized_hunk_hash(hunk_a)
        hash_b = compute_normalized_hunk_hash(hunk_b)
        hash_c = compute_normalized_hunk_hash(hunk_c)

        self.assertEqual(hash_a, hash_b)
        self.assertNotEqual(hash_a, hash_c)


class TestPRSymbolsLogic(unittest.TestCase):
    """Tests Python AST symbol extraction and Decision D2 non-Python fallback."""

    def test_extract_python_symbols(self):
        py_code = """
class Calculator:
    def add(self, a, b):
        return a + b

    async def fetch_result(self):
        return 42

def standalone_func():
    pass
"""
        syms = extract_python_symbols(py_code, file_path="calc.py")
        names = {s.symbol_name for s in syms}
        self.assertIn("Calculator", names)
        self.assertIn("Calculator.add", names)
        self.assertIn("Calculator.fetch_result", names)
        self.assertIn("standalone_func", names)

        # Check types
        type_map = {s.symbol_name: s.symbol_type for s in syms}
        self.assertEqual(type_map["Calculator"], "class")
        self.assertEqual(type_map["Calculator.add"], "method")
        self.assertEqual(type_map["Calculator.fetch_result"], "method")
        self.assertEqual(type_map["standalone_func"], "function")

    def test_decision_d2_non_python_fallback(self):
        # Non-Python files must return empty symbol lists (Decision D2)
        md_content = "# Title\n\ndef fake_func():\n    pass"
        syms = extract_python_symbols(md_content, file_path="docs/spec.md")
        self.assertEqual(syms, [])
        self.assertFalse(is_python_file("docs/spec.md"))
        self.assertFalse(is_python_file("config.json"))
        self.assertTrue(is_python_file("tools/probe.py"))

    def test_find_touched_symbols(self):
        py_code = """class Foo:
    def method_one(self):
        print("one")

    def method_two(self):
        print("two")
"""
        syms = extract_python_symbols(py_code, file_path="foo.py")
        # Line 2-3 is method_one, Line 5-6 is method_two
        hunks_touching_one = [HunkRange(old_start=3, old_lines=1, new_start=3, new_lines=1)]
        hunks_touching_two = [HunkRange(old_start=6, old_lines=1, new_start=6, new_lines=1)]

        touched_one = find_touched_symbols(syms, hunks_touching_one, use_new_coords=True)
        touched_two = find_touched_symbols(syms, hunks_touching_two, use_new_coords=True)

        names_one = {s.symbol_name for s in touched_one}
        names_two = {s.symbol_name for s in touched_two}

        self.assertIn("Foo.method_one", names_one)
        self.assertNotIn("Foo.method_two", names_one)
        self.assertIn("Foo.method_two", names_two)
        self.assertNotIn("Foo.method_one", names_two)


class TestPRDuplicatesAndEngine(unittest.TestCase):
    """Tests duplicate patch/blob detection and pairwise verdicts."""

    def test_detect_duplicate_commits(self):
        c1 = CommitPatchEvidence(commit_sha="aaa", patch_id="pid_123", subject="feat 1")
        c2 = CommitPatchEvidence(commit_sha="bbb", patch_id="pid_456", subject="feat 2")
        c3 = CommitPatchEvidence(commit_sha="ccc", patch_id="pid_123", subject="feat 1 cherry-picked")

        dups = detect_duplicate_commits([c1, c2], [c3])
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["patch_id"], "pid_123")

    def test_detect_duplicate_blobs(self):
        f1 = FileHunkDiff(path="data/spec.json", status="A", blob_sha="blob_999")
        f2 = FileHunkDiff(path="data/spec.json", status="A", blob_sha="blob_999")
        f3 = FileHunkDiff(path="data/spec.json", status="A", blob_sha="blob_888")

        dups = detect_duplicate_blobs({"data/spec.json": f1}, {"data/spec.json": f2})
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["blob_sha"], "blob_999")

        no_dups = detect_duplicate_blobs({"data/spec.json": f1}, {"data/spec.json": f3})
        self.assertEqual(len(no_dups), 0)

    def test_compare_branches_verdicts(self):
        # Case 1: Identical file changes -> IDENTICAL
        b1 = BranchEvidence(
            branch="b1",
            merge_base="mb",
            head_sha="h1",
            commit_count=1,
            files={"a.py": FileHunkDiff(path="a.py", status="M", hunk_hashes=["hash1"])},
        )
        b2 = BranchEvidence(
            branch="b2",
            merge_base="mb",
            head_sha="h2",
            commit_count=1,
            files={"a.py": FileHunkDiff(path="a.py", status="M", hunk_hashes=["hash1"])},
        )
        pair1 = compare_branches(b1, b2)
        self.assertEqual(pair1.verdict, OverlapVerdict.IDENTICAL)

        # Case 2: Overlapping hunks -> OVERLAPPING_HUNKS
        h_a = [HunkRange(old_start=10, old_lines=5, new_start=10, new_lines=5)]
        h_b = [HunkRange(old_start=12, old_lines=4, new_start=12, new_lines=4)]
        b_ov1 = BranchEvidence(
            branch="b_ov1",
            merge_base="mb",
            head_sha="h_ov1",
            commit_count=1,
            files={"b.py": FileHunkDiff(path="b.py", status="M", hunks=h_a, hunk_hashes=["hash_a"])},
        )
        b_ov2 = BranchEvidence(
            branch="b_ov2",
            merge_base="mb",
            head_sha="h_ov2",
            commit_count=1,
            files={"b.py": FileHunkDiff(path="b.py", status="M", hunks=h_b, hunk_hashes=["hash_b"])},
        )
        pair2 = compare_branches(b_ov1, b_ov2)
        self.assertEqual(pair2.verdict, OverlapVerdict.OVERLAPPING_HUNKS)

        # Case 3: Disjoint hunks -> DISJOINT_HUNKS
        h_c = [HunkRange(old_start=100, old_lines=5, new_start=100, new_lines=5)]
        b_dj = BranchEvidence(
            branch="b_dj",
            merge_base="mb",
            head_sha="h_dj",
            commit_count=1,
            files={"b.py": FileHunkDiff(path="b.py", status="M", hunks=h_c, hunk_hashes=["hash_c"])},
        )
        pair3 = compare_branches(b_ov1, b_dj)
        self.assertEqual(pair3.verdict, OverlapVerdict.DISJOINT_HUNKS)

        # Case 4: Disjoint files -> DISJOINT_FILES
        b_df = BranchEvidence(
            branch="b_df",
            merge_base="mb",
            head_sha="h_df",
            commit_count=1,
            files={"other.py": FileHunkDiff(path="other.py", status="A")},
        )
        pair4 = compare_branches(b_ov1, b_df)
        self.assertEqual(pair4.verdict, OverlapVerdict.DISJOINT_FILES)

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = EvidenceCache(cache_dir=Path(tmp_dir))
            b_ev = BranchEvidence(
                branch="feat/test-cache",
                merge_base="mb_sha_12345",
                head_sha="head_sha_67890",
                commit_count=1,
                commits=[CommitPatchEvidence(commit_sha="c1", patch_id="p1", subject="sub")],
                files={"test.py": FileHunkDiff(path="test.py", status="M")},
            )
            cache.set(b_ev)

            retrieved = cache.get("feat/test-cache", "head_sha_67890", "mb_sha_12345")
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.branch, "feat/test-cache")
            self.assertEqual(len(retrieved.commits), 1)
            self.assertIn("test.py", retrieved.files)


if __name__ == "__main__":
    unittest.main()
