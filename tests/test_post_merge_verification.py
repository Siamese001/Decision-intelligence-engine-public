"""Post-merge certification must validate the commit, never authorize direct main commits."""
from __future__ import annotations

import json
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import verify_commit
from tools.verify_commit import _is_read_only_main_check
from tools.verify_post_merge import verify_latest_merge


class PostMergeVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "base.txt")
        self.git("commit", "-m", "chore: initial")
        self.base = self.git("rev-parse", "HEAD")
        self.git("switch", "-c", "fix/example")
        (self.repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        self.git("add", "feature.txt")
        self.git("commit", "-m", "fix: example")
        self.branch_head = self.git("rev-parse", "HEAD")
        self.git("switch", "main")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def merge(self, target: str = "main") -> None:
        message = "Merge branch 'fix/example' into main (Example fix)"
        self.git("merge", "--no-ff", "--no-commit", "fix/example")
        path = self.repo / "reports" / "pr_merge_fix_example_receipt.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "pr_type": "LOCAL_FEATURE_BRANCH_MERGE",
            "branch": "fix/example",
            "head_sha": self.branch_head,
            "branch_head_sha": self.branch_head,
            "main_base_sha": self.base,
            "merge_message": message,
            "target": target,
            "terminal_outcome": "PASS",
        }), encoding="utf-8")
        self.git("add", str(path))
        self.git("commit", "-m", message)

    def test_certifies_only_receipt_delta_from_reviewed_branch(self) -> None:
        self.merge()
        passed, detail = verify_latest_merge(self.repo)
        self.assertTrue(passed, detail)

    def test_rejects_forged_receipt(self) -> None:
        self.merge(target="other")
        passed, detail = verify_latest_merge(self.repo)
        self.assertFalse(passed)
        self.assertIn("target", detail)

    def test_staged_main_commit_still_uses_direct_commit_guard(self) -> None:
        self.merge()
        with patch("tools.verify_commit.ROOT", self.repo):
            self.assertTrue(_is_read_only_main_check())
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(verify_commit.main(["--staged"]), 0)
            self.assertIn("POST-MERGE PASS", output.getvalue())
            (self.repo / "base.txt").write_text("staged change\n", encoding="utf-8")
            self.git("add", "base.txt")
            self.assertFalse(_is_read_only_main_check())
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(verify_commit.main(["--staged"]), 1)
            self.assertIn("DIRECT_MAIN_COMMIT_BLOCKED", output.getvalue())
