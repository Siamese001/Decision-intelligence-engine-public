#!/usr/bin/env python3
"""End-to-end integration test for Operator Decision Vocabulary and Governance."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from config.operator_decision_vocabulary import (
    CANONICAL_VOCABULARY_TERMS,
    disambiguate_term,
)


class TestOperatorDecisionVocabularyE2E(unittest.TestCase):
    """End-to-end verification of vocabulary SSOT, glossary, links, and linter."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.python_bin = cls.repo_root / ".venv" / "bin" / "python"
        if not cls.python_bin.exists():
            cls.python_bin = Path(sys.executable)

    def test_e2e_01_glossary_documentation_exists_and_linked(self) -> None:
        """Verify glossary document exists and is linked from AGENTS.md and hitl-decisions.md."""
        glossary_path = self.repo_root / "governance" / "OPERATOR_DECISION_GLOSSARY.md"
        self.assertTrue(glossary_path.exists(), "OPERATOR_DECISION_GLOSSARY.md must exist")
        glossary_content = glossary_path.read_text(encoding="utf-8")

        for term in CANONICAL_VOCABULARY_TERMS:
            self.assertIn(term, glossary_content, f"Glossary must define canonical term: {term}")

        # Check links in AGENTS.md
        agents_path = self.repo_root / "AGENTS.md"
        self.assertTrue(agents_path.exists())
        self.assertIn(
            "governance/OPERATOR_DECISION_GLOSSARY.md",
            agents_path.read_text(encoding="utf-8"),
            "AGENTS.md must link to OPERATOR_DECISION_GLOSSARY.md",
        )

        # Check links in .agents/rules/hitl-decisions.md
        rules_path = self.repo_root / ".agents" / "rules" / "hitl-decisions.md"
        self.assertTrue(rules_path.exists())
        self.assertIn(
            "OPERATOR_DECISION_GLOSSARY.md",
            rules_path.read_text(encoding="utf-8"),
            "hitl-decisions.md must link to OPERATOR_DECISION_GLOSSARY.md",
        )

    def test_e2e_02_linter_execution_passes_on_repo(self) -> None:
        """Verify tools/lint_operator_decision_terms.py passes across repository."""
        cmd = [str(self.python_bin), "tools/lint_operator_decision_terms.py"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        self.assertEqual(
            res.returncode,
            0,
            f"Linter must pass on clean repo. Stderr: {res.stderr}\nStdout: {res.stdout}",
        )
        self.assertIn("No prohibited legacy terms found", res.stdout)

    def test_e2e_03_linter_lifecycle_fail_and_recover(self) -> None:
        """Verify linter fails on introduced legacy term and recovers when updated to SSOT."""
        test_file = self.repo_root / "tools" / "_temp_vocab_test.py"
        try:
            # Step 1: Write legacy term
            test_file.write_text("def check():\n    return requires_hitl\n", encoding="utf-8")
            cmd = [
                str(self.python_bin),
                "tools/lint_operator_decision_terms.py",
                "--file",
                str(test_file),
            ]
            res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
            self.assertEqual(res.returncode, 1, "Must fail when requires_hitl is introduced")
            self.assertIn("Replaced term 'requires_hitl' found", res.stdout)

            # Step 2: Fix to canonical vocabulary
            test_file.write_text(
                "def check():\n    return needs_operator_decision\n", encoding="utf-8"
            )
            res_fixed = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
            self.assertEqual(
                res_fixed.returncode, 0, "Must pass after replacing with canonical term"
            )
        finally:
            test_file.unlink(missing_ok=True)

    def test_e2e_04_inventory_disambiguation_concordance(self) -> None:
        """Verify disambiguation map resolves all tagged items from Wave 1 inventory."""
        inventory_file = (
            self.repo_root / "reports" / "ask_user_question_enforcement" / "term_inventory.json"
        )
        data = json.loads(inventory_file.read_text(encoding="utf-8"))
        for item in data.get("inventory", []):
            term = item["term"]
            canonical = disambiguate_term(term)
            self.assertIsNotNone(canonical, f"Term '{term}' from inventory must disambiguate")


if __name__ == "__main__":
    unittest.main()
