"""tests/end_to_end/test_tree_digest_attestation_e2e.py

End-to-End test suite for Out-of-Tree Attestations & DSSE Envelopes (Wave 6 / Findings X-02, X-17).
Exercises:
1. Tree digest invariance when volatile reports/** or .local/** paths change.
2. Tree digest sensitivity when code files outside reports/** change.
3. In-toto Statement v1 + DSSE envelope creation, serialization, loading, and verification.
4. Out-of-tree attestation qualification in check_readiness_qualification.
5. Backward-compatible fallback to legacy in-tree receipts.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.attestation_core import (
    IN_TOTO_STATEMENT_TYPE,
    READINESS_PREDICATE_TYPE,
    check_readiness_qualification,
    compute_tree_digest,
    load_attestation_by_tree,
    save_readiness_attestation,
    unwrap_dsse_envelope,
    verify_statement_subject,
)


class TreeDigestAttestationE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent

    def test_tree_digest_computation(self) -> None:
        """Verifies that compute_tree_digest returns a 64-character SHA-256 hex string."""
        digest = compute_tree_digest(self.repo_root)
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        int(digest, 16)  # Must be valid hex

    def test_tree_digest_invariance_for_reports(self) -> None:
        """Verifies that volatile paths under reports/** do not affect the tree digest."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "ci@nee.local"], cwd=tmp_path, check=True
            )
            subprocess.run(["git", "config", "user.name", "CI Bot"], cwd=tmp_path, check=True)

            # Create tracked code file
            (tmp_path / "foo.py").write_text("print('hello')\n", encoding="utf-8")
            subprocess.run(["git", "add", "foo.py"], cwd=tmp_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True
            )

            digest_before = compute_tree_digest(tmp_path)

            # Add volatile reports file
            reports_dir = tmp_path / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            (reports_dir / "timing.json").write_text('{"run": 1}\n', encoding="utf-8")
            subprocess.run(["git", "add", "reports/timing.json"], cwd=tmp_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", "add report"], cwd=tmp_path, check=True, capture_output=True
            )

            digest_after = compute_tree_digest(tmp_path)
            self.assertEqual(
                digest_before,
                digest_after,
                "Tree digest must remain invariant when reports/** changes",
            )

            # Modify code file outside reports/
            (tmp_path / "foo.py").write_text("print('hello world!')\n", encoding="utf-8")
            subprocess.run(["git", "add", "foo.py"], cwd=tmp_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", "update code"],
                cwd=tmp_path,
                check=True,
                capture_output=True,
            )

            digest_code_changed = compute_tree_digest(tmp_path)
            self.assertNotEqual(
                digest_code_changed,
                digest_before,
                "Tree digest must change when code outside reports/ changes",
            )

    def test_dsse_envelope_lifecycle(self) -> None:
        """Tests in-toto Statement creation, DSSE envelope wrapping, saving, and loading."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fake_tree = "a" * 64
            predicate = {
                "branch": "feat/test-feature",
                "head_commit_sha": "b" * 40,
                "terminal_outcome": "PASS",
                "total_stages": 29,
                "stage_results": {"stage_01": "PASS"},
            }

            att_path = save_readiness_attestation(
                repo_root=tmp_path,
                predicate=predicate,
                tree_digest=fake_tree,
            )
            self.assertTrue(att_path.exists())
            self.assertEqual(att_path.name, f"{fake_tree}.json")

            raw_envelope = json.loads(att_path.read_text(encoding="utf-8"))
            self.assertEqual(raw_envelope.get("payloadType"), "application/vnd.in-toto+json")
            self.assertIn("payload", raw_envelope)
            self.assertIn("signatures", raw_envelope)

            stmt = unwrap_dsse_envelope(raw_envelope)
            self.assertEqual(stmt["_type"], IN_TOTO_STATEMENT_TYPE)
            self.assertEqual(stmt["predicateType"], READINESS_PREDICATE_TYPE)
            self.assertEqual(stmt["predicate"]["branch"], "feat/test-feature")
            self.assertTrue(verify_statement_subject(stmt, fake_tree))

            loaded_stmt = load_attestation_by_tree(tmp_path, fake_tree)
            self.assertIsNotNone(loaded_stmt)
            self.assertEqual(loaded_stmt["predicate"]["terminal_outcome"], "PASS")

    def test_check_readiness_qualification_out_of_tree(self) -> None:
        """Verifies that check_readiness_qualification affirms valid out-of-tree DSSE attestations."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "ci@nee.local"], cwd=tmp_path, check=True
            )
            subprocess.run(["git", "config", "user.name", "CI Bot"], cwd=tmp_path, check=True)

            (tmp_path / "test.py").write_text("a = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "test.py"], cwd=tmp_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True
            )

            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            tree_digest = compute_tree_digest(tmp_path, ref=head_sha)

            # Unqualified before attestation
            passed, stmt, _ = check_readiness_qualification(
                repo_root=tmp_path,
                branch="feat/test-branch",
                head_sha=head_sha,
                slug="feat_test_branch",
            )
            self.assertFalse(passed)
            self.assertIsNone(stmt)

            # Qualified after saving out-of-tree attestation
            predicate = {
                "branch": "feat/test-branch",
                "head_commit_sha": head_sha,
                "terminal_outcome": "PASS",
            }
            save_readiness_attestation(
                repo_root=tmp_path,
                predicate=predicate,
                tree_digest=tree_digest,
                head_sha=head_sha,
            )

            passed, stmt, desc = check_readiness_qualification(
                repo_root=tmp_path,
                branch="feat/test-branch",
                head_sha=head_sha,
                slug="feat_test_branch",
            )
            self.assertTrue(passed)
            self.assertIsNotNone(stmt)
            self.assertIn("out-of-tree DSSE attestation", desc)

    def test_check_readiness_qualification_legacy_fallback(self) -> None:
        """Verifies backward compatibility with legacy in-tree reports/readiness/... receipt."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "ci@nee.local"], cwd=tmp_path, check=True
            )
            subprocess.run(["git", "config", "user.name", "CI Bot"], cwd=tmp_path, check=True)

            (tmp_path / "test.py").write_text("a = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "test.py"], cwd=tmp_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True
            )

            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()

            legacy_dir = tmp_path / "reports" / "readiness"
            legacy_dir.mkdir(parents=True, exist_ok=True)
            legacy_path = legacy_dir / "local_main_readiness_feat_legacy_branch_receipt.json"

            legacy_receipt = {
                "branch": "feat/legacy-branch",
                "head_commit_sha": head_sha,
                "terminal_outcome": "PASS",
                "total_stages": 28,
            }
            legacy_path.write_text(json.dumps(legacy_receipt), encoding="utf-8")

            passed, data, desc = check_readiness_qualification(
                repo_root=tmp_path,
                branch="feat/legacy-branch",
                head_sha=head_sha,
                slug="feat_legacy_branch",
            )
            self.assertTrue(passed)
            self.assertEqual(data["terminal_outcome"], "PASS")
            self.assertIn("legacy in-tree receipt", desc)


if __name__ == "__main__":
    unittest.main()
