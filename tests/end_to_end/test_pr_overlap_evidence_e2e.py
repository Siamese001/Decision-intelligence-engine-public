"""
End-to-End lifecycle test for PR Overlap Evidence Layer.

Initializes a synthetic multi-branch fixture git repository, runs
tools/pr_overlap_audit.py evidence, and verifies matrix verdicts and receipts.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


class TestPROverlapEvidenceE2E(unittest.TestCase):
    """E2E test verifying full CLI evidence extraction across a multi-branch git fixture."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name).resolve()

        # Initialize scratch git repository
        _git(["init", "-b", "main"], self.repo)
        _git(["config", "user.name", "Test Agent"], self.repo)
        _git(["config", "user.email", "agent@test.internal"], self.repo)
        _git(["config", "commit.gpgsign", "false"], self.repo)

        # Base commit
        base_py = """# Base module
def func_a():
    x = 1
    y = 2
    return x + y

# Spacer lines to ensure disjoint line ranges
# Line 10
# Line 11
# Line 12
# Line 13
# Line 14
# Line 15
# Line 16
# Line 17
# Line 18
# Line 19
# Line 20

def func_b():
    msg = "hello"
    return msg
"""
        (self.repo / "base.py").write_text(base_py, encoding="utf-8")
        (self.repo / "readme.md").write_text("# Readme\n", encoding="utf-8")
        _git(["add", "base.py", "readme.md"], self.repo)
        _git(["commit", "-m", "chore: initial commit on main"], self.repo)

        # Branch 1: feat/duplicate-a
        _git(["checkout", "-b", "feat/duplicate-a"], self.repo)
        dup_a = base_py.replace("x = 1", "x = 100")
        (self.repo / "base.py").write_text(dup_a, encoding="utf-8")
        _git(["add", "base.py"], self.repo)
        _git(["commit", "-m", "feat: update func_a x value"], self.repo)

        # Branch 2: feat/duplicate-b (cherry-pick / identical patch)
        _git(["checkout", "main"], self.repo)
        _git(["checkout", "-b", "feat/duplicate-b"], self.repo)
        (self.repo / "base.py").write_text(dup_a, encoding="utf-8")
        _git(["add", "base.py"], self.repo)
        _git(["commit", "-m", "feat: update func_a x value cherry-pick"], self.repo)

        # Branch 3: feat/disjoint (modifies func_b at line 23)
        _git(["checkout", "main"], self.repo)
        _git(["checkout", "-b", "feat/disjoint"], self.repo)
        disjoint_py = base_py.replace('msg = "hello"', 'msg = "world"')
        (self.repo / "base.py").write_text(disjoint_py, encoding="utf-8")
        _git(["add", "base.py"], self.repo)
        _git(["commit", "-m", "feat: update func_b msg"], self.repo)

        # Branch 4: feat/overlap (modifies func_a x value differently, overlapping lines)
        _git(["checkout", "main"], self.repo)
        _git(["checkout", "-b", "feat/overlap"], self.repo)
        overlap_py = base_py.replace("x = 1", "x = 999")
        (self.repo / "base.py").write_text(overlap_py, encoding="utf-8")
        _git(["add", "base.py"], self.repo)
        _git(["commit", "-m", "feat: conflict edit on func_a"], self.repo)

        # Branch 5: feat/blob-dup-a (adds new json file)
        _git(["checkout", "main"], self.repo)
        _git(["checkout", "-b", "feat/blob-dup-a"], self.repo)
        (self.repo / "shared.json").write_text('{"key": "value"}\n', encoding="utf-8")
        _git(["add", "shared.json"], self.repo)
        _git(["commit", "-m", "feat: add shared json config A"], self.repo)

        # Branch 6: feat/blob-dup-b (adds same json file identically)
        _git(["checkout", "main"], self.repo)
        _git(["checkout", "-b", "feat/blob-dup-b"], self.repo)
        (self.repo / "shared.json").write_text('{"key": "value"}\n', encoding="utf-8")
        _git(["add", "shared.json"], self.repo)
        _git(["commit", "-m", "feat: add shared json config B"], self.repo)

        _git(["checkout", "main"], self.repo)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_pr_overlap_audit_e2e(self):
        receipt_path = self.repo / "reports" / "wave3_receipt.json"

        # Record repo status before run
        status_before = _git(["status", "--porcelain"], self.repo).stdout

        cmd = [
            sys.executable,
            str(ROOT / "tools" / "pr_overlap_audit.py"),
            "evidence",
            "--fixture",
            str(self.repo),
            "--base",
            "main",
            "--receipt",
            str(receipt_path),
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"pr_overlap_audit failed: {proc.stderr}")

        # 1. Verify JSON output
        report = json.loads(proc.stdout)
        self.assertEqual(report["schema_version"], "urn:nee:pr-overlap-evidence:1.0.0")
        self.assertIn("pairwise_matrix", report)

        matrix = report["pairwise_matrix"]

        # 2. Check Pairwise Verdicts
        # duplicate-a vs duplicate-b must be IDENTICAL or DUPLICATE_OF
        dup_pair = matrix.get("feat/duplicate-a", {}).get("feat/duplicate-b")
        self.assertIsNotNone(dup_pair)
        self.assertIn(dup_pair["verdict"], ["IDENTICAL", "DUPLICATE_OF"])

        # duplicate-a vs disjoint must be DISJOINT_HUNKS
        disjoint_pair = matrix.get("feat/disjoint", {}).get("feat/duplicate-a") or matrix.get("feat/duplicate-a", {}).get("feat/disjoint")
        self.assertIsNotNone(disjoint_pair)
        self.assertEqual(disjoint_pair["verdict"], "DISJOINT_HUNKS")

        # duplicate-a vs overlap must be OVERLAPPING_HUNKS
        overlap_pair = matrix.get("feat/duplicate-a", {}).get("feat/overlap") or matrix.get("feat/overlap", {}).get("feat/duplicate-a")
        self.assertIsNotNone(overlap_pair)
        self.assertEqual(overlap_pair["verdict"], "OVERLAPPING_HUNKS")

        # blob-dup-a vs blob-dup-b must be DUPLICATE_OF or IDENTICAL
        blob_pair = matrix.get("feat/blob-dup-a", {}).get("feat/blob-dup-b") or matrix.get("feat/blob-dup-b", {}).get("feat/blob-dup-a")
        self.assertIsNotNone(blob_pair)
        self.assertIn(blob_pair["verdict"], ["IDENTICAL", "DUPLICATE_OF"])

        # 3. Verify Receipt on disk
        self.assertTrue(receipt_path.exists())
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["terminal_outcome"], "PASS")
        self.assertEqual(receipt["schema_version"], "urn:nee:pr-overlap-evidence:1.0.0")
        self.assertGreater(receipt["branches_inspected"], 4)

        # 4. Invariant: zero repo mutation
        status_after = _git(["status", "--porcelain"], self.repo).stdout
        self.assertEqual(
            [line for line in status_after.splitlines() if not line.endswith("reports/") and not line.endswith("wave3_receipt.json") and not line.endswith(".local/")],
            [line for line in status_before.splitlines() if not line.endswith("reports/") and not line.endswith("wave3_receipt.json") and not line.endswith(".local/")],
        )


if __name__ == "__main__":
    unittest.main()
