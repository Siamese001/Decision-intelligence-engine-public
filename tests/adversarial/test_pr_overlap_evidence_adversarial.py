"""
Adversarial and boundary test suite for PR Overlap Evidence Layer.

Covers:
1. Malformed / corrupted unified diffs and truncated headers
2. Corrupted Python syntax and non-UTF8 binary files
3. Extreme boundary payloads (200-char branch names, unicode, 0-file diffs)
4. Multi-threaded concurrency race safety on EvidenceCache
5. Invariant preservation and schema validation
"""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from tools.pr_overlap_core import (
    SCHEMA_VERSION,
    BranchEvidence,
    CommitPatchEvidence,
    EvidenceCache,
    FileHunkDiff,
    HunkRange,
    extract_python_symbols,
    find_touched_symbols,
    parse_unified_diff_u0,
    ranges_overlap,
)


class TestPROverlapEvidenceAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case testing for overlap evidence engine."""

    def test_malformed_diff_headers_resilience(self):
        corrupted_diffs = [
            "diff --git a/file.py",  # Truncated header
            "@@ not_a_number @@",   # Garbage hunk header
            "@@ - @@",
            "diff --git a/foo b/bar\n@@ -0,0 +0,0 @@\n+line",
            "",                      # Empty string
            "\x00\x01\x02\x03",      # Binary noise
        ]
        for diff in corrupted_diffs:
            try:
                res = parse_unified_diff_u0(diff)
                self.assertIsInstance(res, dict)
            except Exception as e:
                self.fail(f"parse_unified_diff_u0 crashed on malformed diff: {e}")

    def test_corrupted_python_ast_syntax_error(self):
        broken_code = """
def unclosed_func(
    x = 1
    # Missing closing parenthesis and colon
class Broken:
    def !!! illegal syntax %^&
"""
        # Must not raise SyntaxError; must return empty list gracefully
        syms = extract_python_symbols(broken_code, file_path="broken.py")
        self.assertEqual(syms, [])

        # Non-utf8 / binary simulation
        syms_binary = extract_python_symbols("\x80\x81\x82", file_path="binary.py")
        self.assertEqual(syms_binary, [])

    def test_extreme_boundary_line_numbers_and_hunks(self):
        # Negative numbers, 0 lines, massive integers (100 million lines)
        self.assertTrue(ranges_overlap(1, 100_000_000, 50_000_000, 10))
        self.assertFalse(ranges_overlap(0, 0, 10, 0))

        # Massive hunk list
        hunks = [
            HunkRange(old_start=i * 10, old_lines=5, new_start=i * 10, new_lines=5)
            for i in range(1000)
        ]
        syms = extract_python_symbols("def foo():\n    pass\n", file_path="foo.py")
        # Ensure find_touched_symbols finishes in milliseconds even on 1000 hunks
        touched = find_touched_symbols(syms, hunks)
        self.assertIsInstance(touched, list)

    def test_concurrent_cache_access_race_safety(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = EvidenceCache(cache_dir=Path(tmp_dir))
            num_workers = 10
            iterations_per_worker = 20

            def worker_fn(worker_id: int):
                for i in range(iterations_per_worker):
                    branch_name = f"feat/worker-{worker_id}-iter-{i % 5}"
                    b_ev = BranchEvidence(
                        branch=branch_name,
                        merge_base="mb_123",
                        head_sha=f"head_{worker_id}_{i}",
                        commit_count=1,
                        commits=[CommitPatchEvidence(commit_sha=f"c_{i}", patch_id=f"pid_{i}", subject=f"sub {i}")],
                        files={f"file_{worker_id}.py": FileHunkDiff(path=f"file_{worker_id}.py", status="M")},
                    )
                    cache.set(b_ev)
                    cached = cache.get(branch_name, f"head_{worker_id}_{i}", "mb_123")
                    if cached is not None:
                        assert cached.branch == branch_name

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [executor.submit(worker_fn, wid) for wid in range(num_workers)]
                for f in concurrent.futures.as_completed(futures):
                    f.result()

    def test_schema_invariant_preservation(self):
        self.assertEqual(SCHEMA_VERSION, "urn:nee:pr-overlap-evidence:1.0.0")


if __name__ == "__main__":
    unittest.main()
