"""Adversarial and edge-case security tests for storage hygiene tools."""
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from tools.storage_hygiene_core.pins import resolve_pins
from tools.storage_hygiene_core.policy import (
    GenerationPolicy,
    PolicyValidationError,
    QuarantinePolicy,
    ReportsPolicy,
    StorageRetentionPolicy,
    TranscriptCachePolicy,
)
from tools.storage_hygiene_core.receipt import create_hygiene_report, verify_report_digest


class TestStorageHygieneAdversarial(unittest.TestCase):
    def test_ast_invariant_no_deletion_primitives(self):
        """Verify that read-only storage hygiene core modules contain no deletion calls."""
        core_dir = Path(__file__).resolve().parents[2] / "tools" / "storage_hygiene_core"
        forbidden_funcs = {"remove", "unlink", "rmtree"}

        for pyfile in core_dir.glob("*.py"):
            tree = ast.parse(pyfile.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    # Check func.id or func.attr
                    if isinstance(node.func, ast.Name) and node.func.id in forbidden_funcs:
                        self.fail(
                            f"Forbidden deletion primitive '{node.func.id}' found in {pyfile.name}"
                        )
                    elif isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_funcs:
                        self.fail(
                            f"Forbidden deletion method '{node.func.attr}' found in {pyfile.name}"
                        )

    def test_path_traversal_payload_in_active_pair(self):
        """Active pair attempting path traversal to /etc/passwd or outside root is treated as invalid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            pair_path = tmp_root / ".local" / "dual_pipeline" / "active_pair.json"
            pair_path.parent.mkdir(parents=True)
            pair_payload = {
                "generation_id": "generation-malicious-traversal",
                "graph_path": "../../../../../../../../etc/passwd",
            }
            pair_path.write_text(json.dumps(pair_payload), encoding="utf-8")

            res = resolve_pins(repo_root=tmp_root, runtime_root=tmp_root / ".local" / "dual_pipeline")
            self.assertFalse(res.fail_closed)
            # The malicious traversal must NOT be added to active_generations
            self.assertTrue(
                any(
                    "graph_target_outside_repo" in s.reason
                    or "graph_target_missing" in s.reason
                    for s in res.invalid_sources
                )
            )

    def test_boundary_payloads_in_policies(self):
        """Verify strict parameter validation boundaries."""
        with self.assertRaises(PolicyValidationError):
            GenerationPolicy(rollback_depth=-5).validate()

        with self.assertRaises(PolicyValidationError):
            TranscriptCachePolicy(wav_ttl_days=0).validate()

        with self.assertRaises(PolicyValidationError):
            ReportsPolicy(archive_age_days=-1).validate()

        with self.assertRaises(PolicyValidationError):
            QuarantinePolicy(purge_ttl_days=0).validate()

        with self.assertRaises(PolicyValidationError):
            StorageRetentionPolicy(never_touch=()).validate()

    def test_tampered_receipt_signature_rejection(self):
        """Any payload modification breaks canonical digest verification."""
        pins = {
            "active_generations": ["generation-test-1"],
            "rollback_generations": [],
            "preserved_generations": [],
            "invalid_sources": [],
        }
        summary = {"total_candidates": 5, "total_pinned": 1, "reclaimable_bytes": 1000}
        report = create_hygiene_report(
            mode="plan",
            scheduled=False,
            policy_digest="0" * 64,
            pins=pins,
            summary=summary,
        )

        # Altering scheduled flag
        report["scheduled"] = True
        self.assertFalse(verify_report_digest(report))

        # Restoring scheduled flag, altering mode
        report["scheduled"] = False
        report["mode"] = "apply"
        self.assertFalse(verify_report_digest(report))


if __name__ == "__main__":
    unittest.main()
