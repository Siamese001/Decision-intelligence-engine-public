"""tests/adversarial/test_attestation_tamper_adversarial.py

Adversarial test suite for Out-of-Tree Attestation Tampering & Root Hygiene Guardrails (Wave 6).
Exercises:
1. Tampered tree digest in DSSE envelope subject (fail-closed rejection).
2. Attestation predicate with terminal_outcome="FAIL" (fail-closed rejection).
3. Root File Allowlist violation detection (audit_root_and_size_hygiene catches unauthorized root file).
4. File Size Guard violation detection (audit_root_and_size_hygiene catches >5 MB non-grandfathered file).
5. Corrupted or malformed DSSE JSON payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.attestation_core import (
    check_readiness_qualification,
    load_attestation_by_tree,
    save_readiness_attestation,
)
from tools.audit_root_hygiene import audit_root_and_size_hygiene


class AttestationTamperAdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent

    def test_tampered_subject_digest_rejected(self) -> None:
        """Verifies that if the envelope's subject digest does not match the queried tree digest, it fails."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            tree_a = "1" * 64
            tree_b = "2" * 64

            predicate = {"branch": "feat/tamper", "terminal_outcome": "PASS"}
            save_readiness_attestation(
                repo_root=tmp_path,
                predicate=predicate,
                tree_digest=tree_a,
            )

            # If an attacker renames tree_a.json to tree_b.json:
            receipts_dir = tmp_path / ".local" / "receipts"
            (receipts_dir / f"{tree_b}.json").write_text(
                (receipts_dir / f"{tree_a}.json").read_text()
            )

            # Loading by tree_b must detect subject digest mismatch and reject
            loaded = load_attestation_by_tree(tmp_path, tree_b)
            self.assertIsNone(
                loaded, "Tampered tree digest must be rejected by verify_statement_subject"
            )

    def test_failed_outcome_attestation_rejected(self) -> None:
        """Verifies that an attestation with terminal_outcome='FAIL' fails qualification."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "ci@nee.local"], cwd=tmp_path, check=True
            )
            subprocess.run(["git", "config", "user.name", "CI Bot"], cwd=tmp_path, check=True)

            (tmp_path / "foo.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "foo.py"], cwd=tmp_path, check=True)
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

            from tools.attestation_core import compute_tree_digest

            tree_digest = compute_tree_digest(tmp_path, ref=head_sha)

            predicate = {
                "branch": "feat/failing",
                "head_commit_sha": head_sha,
                "terminal_outcome": "FAIL",
            }
            save_readiness_attestation(
                repo_root=tmp_path,
                predicate=predicate,
                tree_digest=tree_digest,
                head_sha=head_sha,
            )

            passed, _, desc = check_readiness_qualification(
                repo_root=tmp_path,
                branch="feat/failing",
                head_sha=head_sha,
                slug="feat_failing",
            )
            self.assertFalse(passed)
            self.assertEqual(desc, "none")

    def test_malformed_dsse_envelope_handling(self) -> None:
        """Verifies that corrupted envelope JSON or invalid base64 fails gracefully without crash."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            tree = "3" * 64
            receipts_dir = tmp_path / ".local" / "receipts"
            receipts_dir.mkdir(parents=True, exist_ok=True)

            # Corrupted JSON
            (receipts_dir / f"{tree}.json").write_text("{not valid json", encoding="utf-8")
            self.assertIsNone(load_attestation_by_tree(tmp_path, tree))

            # Invalid base64
            corrupt_envelope = {
                "payloadType": "application/vnd.in-toto+json",
                "payload": "!!not base64!!",
            }
            (receipts_dir / f"{tree}.json").write_text(
                json.dumps(corrupt_envelope), encoding="utf-8"
            )
            self.assertIsNone(load_attestation_by_tree(tmp_path, tree))

    def test_root_allowlist_catches_unauthorized_root_file(self) -> None:
        """Verifies that an unapproved file in root is caught as a violation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

            cfg_dir = tmp_path / "config" / "governance"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = cfg_dir / "root_file_allowlist.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "allowed_root_files": ["README.md", "pyproject.toml"],
                        "max_file_size_bytes": 5242880,
                        "grandfathered_large_files": [],
                    }
                ),
                encoding="utf-8",
            )

            (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
            (tmp_path / "random_scratch.txt").write_text("scratch\n", encoding="utf-8")

            subprocess.run(
                ["git", "add", "README.md", "random_scratch.txt"], cwd=tmp_path, check=True
            )

            ok, violations = audit_root_and_size_hygiene(
                tmp_path, staged_only=False, config_path=cfg_path
            )
            self.assertFalse(ok)
            self.assertTrue(
                any("[ROOT_FILE_ALLOWLIST_VIOLATION] 'random_scratch.txt'" in v for v in violations)
            )

    def test_file_size_guard_catches_oversized_file(self) -> None:
        """Verifies that a file exceeding max_file_size_bytes is caught unless grandfathered."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

            cfg_dir = tmp_path / "config" / "governance"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            cfg_path = cfg_dir / "root_file_allowlist.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "allowed_root_files": ["README.md"],
                        "max_file_size_bytes": 1024,
                        "grandfathered_large_files": ["data/grandfathered.bin"],
                    }
                ),
                encoding="utf-8",
            )

            data_dir = tmp_path / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            (data_dir / "oversized.bin").write_bytes(b"0" * 2048)
            (data_dir / "grandfathered.bin").write_bytes(b"0" * 2048)

            subprocess.run(
                ["git", "add", "data/oversized.bin", "data/grandfathered.bin"],
                cwd=tmp_path,
                check=True,
            )

            ok, violations = audit_root_and_size_hygiene(
                tmp_path, staged_only=False, config_path=cfg_path
            )
            self.assertFalse(ok)
            self.assertTrue(any("data/oversized.bin" in v for v in violations))
            self.assertFalse(any("data/grandfathered.bin" in v for v in violations))


if __name__ == "__main__":
    unittest.main()
