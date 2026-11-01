"""Ratchet and adversarial tests for EXEMPT_FILES reduction in lint_hardcoded_parameters (Wave 3)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import CANONICAL_EMBEDDING_MODEL_ID
from tools.lint_hardcoded_parameters import EXEMPT_FILES, scan_file

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestSSOTExemptFilesRatchet(unittest.TestCase):
    """Enforce strict ceiling on EXEMPT_FILES and active linting of previously exempted modules."""

    def test_exempt_files_ratchet_ceiling(self) -> None:
        allowed_owners = {
            "dual_pipeline/model_registry.py",
            "dual_pipeline/parameters.py",
            "dual_pipeline/parameters_ops.py",
            "dual_pipeline/parameters_subsystems.py",
            "dual_pipeline/release_thresholds.py",
            "dual_pipeline/token_optimization/schemas.py",
        }
        self.assertLessEqual(len(EXEMPT_FILES), 6)
        self.assertEqual(set(EXEMPT_FILES), allowed_owners)

    def test_previously_exempted_modules_are_actively_linted(self) -> None:
        must_not_be_exempt = (
            "config/credentials.py",
            "dual_pipeline/transcript_stages_core/recap_modular_dag.py",
            "dual_pipeline/roundtable_orchestrator.py",
            "dual_pipeline/index_core/embedding_space_contract.py",
            "dual_pipeline/completion_core/guard.py",
            "tools/pr_governance_core/common.py",
        )
        for rel in must_not_be_exempt:
            self.assertNotIn(rel, EXEMPT_FILES)
            self.assertEqual(scan_file(REPO_ROOT / rel, REPO_ROOT), [])

    def test_adversarial_violation_in_recap_modular_dag_is_caught(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            root = Path(tmp)
            target = root / "dual_pipeline" / "transcript_stages_core" / "recap_modular_dag.py"
            target.parent.mkdir(parents=True)
            target.write_text(
                "def _get_tier1_word_limits():\n    return (550, 900)\n",
                encoding="utf-8",
            )
            violations = scan_file(target, root)
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].rule, "LOCAL_WORD_LIMIT_FUNCTION")

    def test_canonical_embedding_model_id_ssot(self) -> None:
        self.assertEqual(CANONICAL_EMBEDDING_MODEL_ID, "BAAI/bge-m3")  # noqa: SSOT_EXEMPT


if __name__ == "__main__":
    unittest.main()
