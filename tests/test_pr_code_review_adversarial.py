"""
Adversarial Test Suite for PR Semantic Code Review (Wave 3).
Tests fail-closed detection of mass reformats, whitespace diffs, stale paths,
unsigned ledger entries, and name/content mismatches against real temporary git repos.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.code_review import compute_code_review
from tools.pr_governance_core.code_review_rules import ReviewVerdict


class TestPRCodeReviewAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp_dir.name)
        self._git(["init", "-b", "main"])
        self._git(["config", "user.name", "ReviewTester"])
        self._git(["config", "user.email", "review@example.com"])

        # Initial commit on main
        (self.repo_root / "init.txt").write_text("initial commit\n", encoding="utf-8")
        self._git(["add", "init.txt"])
        self._git(["commit", "-m", "chore: initial commit"])

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _git(self, cmd: list[str]) -> str:
        res = subprocess.run(
            ["git"] + cmd,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()

    def test_adversarial_mass_reformat_triggers_split_required(self):
        """A single commit touching >300 files must fail into SPLIT_REQUIRED."""
        self._git(["checkout", "-b", "feat/reformat-branch", "main"])
        src_dir = self.repo_root / "dual_pipeline" / "generated"
        src_dir.mkdir(parents=True, exist_ok=True)
        for i in range(305):
            (src_dir / f"mod_{i}.py").write_text(f"# module {i}\n", encoding="utf-8")
        self._git(["add", "dual_pipeline/"])
        self._git(["commit", "-m", "chore: mass formatting touch across 305 files"])

        review = compute_code_review("feat/reformat-branch", base_branch="main", root=self.repo_root, use_cache=False)
        self.assertEqual(review.verdict, ReviewVerdict.SPLIT_REQUIRED)
        self.assertTrue(review.signals.has_mass_reformat_commit)
        self.assertGreaterEqual(review.signals.max_files_in_single_commit, 305)

    def test_adversarial_whitespace_only_triggers_tombstone(self):
        """A branch introducing only trailing whitespace or blank lines must resolve to SUPERSEDED_TOMBSTONE."""
        # Main has file
        (self.repo_root / "dual_pipeline" / "core.py").parent.mkdir(parents=True, exist_ok=True)
        (self.repo_root / "dual_pipeline" / "core.py").write_text("def run():\n    return True\n", encoding="utf-8")
        self._git(["add", "dual_pipeline/core.py"])
        self._git(["commit", "-m", "feat: add core module"])

        self._git(["checkout", "-b", "feat/whitespace-touch", "main"])
        # Add trailing whitespace and extra blank line
        (self.repo_root / "dual_pipeline" / "core.py").write_text("def run():   \n\n    return True  \n", encoding="utf-8")
        self._git(["add", "dual_pipeline/core.py"])
        self._git(["commit", "-m", "style: whitespace adjustments"])

        review = compute_code_review("feat/whitespace-touch", base_branch="main", root=self.repo_root, use_cache=False)
        self.assertEqual(review.verdict, ReviewVerdict.SUPERSEDED_TOMBSTONE)
        self.assertTrue(review.signals.is_whitespace_or_import_only)

    def test_adversarial_stale_path_deleted_on_main_triggers_rebase_retarget(self):
        """A branch modifying a path that was deleted on main since fork must resolve to REBASE_RETARGET."""
        (self.repo_root / "dual_pipeline" / "legacy.py").parent.mkdir(parents=True, exist_ok=True)
        (self.repo_root / "dual_pipeline" / "legacy.py").write_text("def legacy(): pass\n", encoding="utf-8")
        self._git(["add", "dual_pipeline/legacy.py"])
        self._git(["commit", "-m", "feat: add legacy module"])

        # Branch off main and modify legacy.py
        self._git(["checkout", "-b", "feat/legacy-enhancement", "main"])
        (self.repo_root / "dual_pipeline" / "legacy.py").write_text("def legacy(): return 'improved'\n", encoding="utf-8")
        self._git(["add", "dual_pipeline/legacy.py"])
        self._git(["commit", "-m", "feat: enhance legacy"])

        # Main deletes legacy.py
        self._git(["checkout", "main"])
        self._git(["rm", "dual_pipeline/legacy.py"])
        self._git(["commit", "-m", "refactor: remove deprecated legacy module"])

        review = compute_code_review("feat/legacy-enhancement", base_branch="main", root=self.repo_root, use_cache=False)
        self.assertEqual(review.verdict, ReviewVerdict.REBASE_RETARGET)
        self.assertIn("dual_pipeline/legacy.py", review.signals.stale_paths)

    def test_adversarial_unsigned_ledger_triggers_governance_block(self):
        """Adding unsigned decision entries to hitl_decision_ledger.json must trigger GOVERNANCE_BLOCK."""
        (self.repo_root / "hitl_decision_ledger.json").write_text('{\n  "decisions": []\n}\n', encoding="utf-8")
        self._git(["add", "hitl_decision_ledger.json"])
        self._git(["commit", "-m", "chore: initialize ledger"])

        self._git(["checkout", "-b", "feat/malicious-ledger-injection", "main"])
        rogue_content = (
            '{\n'
            '  "decisions": [\n'
            '    {\n'
            '      "decision_id": "D1_ROGUE_OVERRIDE",\n'
            '      "resolution": "Bypass gate",\n'
            '      "confidence": 0.99\n'
            '    }\n'
            '  ]\n'
            '}\n'
        )
        (self.repo_root / "hitl_decision_ledger.json").write_text(rogue_content, encoding="utf-8")
        self._git(["add", "hitl_decision_ledger.json"])
        self._git(["commit", "-m", "feat: rogue unsigned decision entry"])

        review = compute_code_review("feat/malicious-ledger-injection", base_branch="main", root=self.repo_root, use_cache=False)
        self.assertEqual(review.verdict, ReviewVerdict.GOVERNANCE_BLOCK)
        self.assertTrue(any("Unsigned HITL decision" in h for h in review.signals.governance_hazards))

    def test_adversarial_name_content_mismatch_detected(self):
        """Branch named feat/* that touches only documentation must flag name_content_mismatch."""
        self._git(["checkout", "-b", "feat/critical-speedup", "main"])
        (self.repo_root / "README.md").write_text("Just updating the docs\n", encoding="utf-8")
        self._git(["add", "README.md"])
        self._git(["commit", "-m", "docs: update readme text only"])

        review = compute_code_review("feat/critical-speedup", base_branch="main", root=self.repo_root, use_cache=False)
        self.assertTrue(review.signals.name_content_mismatch)

    def test_adversarial_hunk_extraction_and_secret_hazard_detected(self):
        """Diff adding raw os.environ API key and bare git config must populate file_hunks and hazards."""
        self._git(["checkout", "-b", "feat/hazard-injection", "main"])
        src_file = self.repo_root / "dual_pipeline" / "api.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text(
            "import os\n\n"
            "def fetch_data():\n"
            "    key = os.environ.get('OPENAI_API_KEY')\n"
            "    return key\n",
            encoding="utf-8",
        )
        self._git(["add", "dual_pipeline/api.py"])
        self._git(["commit", "-m", "feat: inject api key lookup"])

        review = compute_code_review("feat/hazard-injection", base_branch="main", root=self.repo_root, use_cache=False)
        self.assertTrue(any("Raw os.environ secret access" in h for h in review.signals.governance_hazards))
        self.assertTrue(len(review.signals.file_hunks) > 0)
        self.assertIn("def fetch_data():", review.signals.modified_ast_symbols)


if __name__ == "__main__":
    unittest.main()
