"""
tests/test_lint_no_transcript_overfitting.py

Unit tests for tools/lint_no_transcript_overfitting.py verifying that all variants of
meeting-specific healers, keyword dispatch cascades, and canned text injections are detected.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_no_transcript_overfitting import (
    check_code_content,
    check_file_naming,
)


class TestLintNoTranscriptOverfitting(unittest.TestCase):
    def test_flags_forbidden_file_names(self):
        bad_names = [
            "neet_healer.py",
            "nexus_healer.py",
            "jake_amit_miles_healer.py",
            "lens_catchup_0919_healer.py",
            "lens_catchup_healer.py",
            "mid_sprint_healer.py",
            "graph_post_jake_amit_miles.py",
            "graph_post_mid_sprint.py",
            "strategic_graph_healer.py",
            "strategic_graph_evidence.py",
            "strategic_graph_proposals.py",
        ]
        for name in bad_names:
            fake_path = Path("/repo/dual_pipeline/transcript_qa_core") / name
            violations = check_file_naming(fake_path)
            self.assertTrue(len(violations) > 0, f"Expected violation for {name}")
            self.assertIn("Meeting-specific file forbidden", violations[0])

    def test_passes_clean_file_name(self):
        clean_names = [
            "gate2.py",
            "healing_mesh.py",
            "table_healer.py",
            "graph_healer.py",
            "extractor.py",
            "recap.py",
        ]
        for name in clean_names:
            fake_path = Path("/repo/dual_pipeline/transcript_qa_core") / name
            violations = check_file_naming(fake_path)
            self.assertEqual(len(violations), 0, f"Unexpected violation for clean name {name}")

    def test_flags_meeting_specific_identifiers_in_ast(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write("""
def heal_neet_output_2(text, meta, changes):
    is_mid_sprint = True
    return text
""")
            tf_path = Path(tf.name)

        try:
            violations = check_code_content(tf_path)
            self.assertTrue(len(violations) >= 2)
            self.assertTrue(any("heal_neet_output_2" in v for v in violations))
            self.assertTrue(any("is_mid_sprint" in v for v in violations))
        finally:
            tf_path.unlink(missing_ok=True)

    def test_flags_synthetic_string_injections(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write("""
# Harmless comment
strat_block = "| Observation | Consequence | ev-910-04 |"
coach_text = "Emphasized coaching future candidates during interview."
""")
            tf_path = Path(tf.name)

        try:
            violations = check_code_content(tf_path)
            self.assertTrue(len(violations) >= 2)
            self.assertTrue(any("ev-910" in v for v in violations))
            self.assertTrue(any("coaching future candidates" in v for v in violations))
        finally:
            tf_path.unlink(missing_ok=True)

    def test_passes_clean_generic_structural_code(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write("""
def normalize_date_format(text: str) -> str:
    import re
    # Converts YYYY-MM-DD to MM/DD/YYYY
    return re.sub(r'(\\d{4})-(\\d{2})-(\\d{2})', r'\\2/\\3/\\1', text)
""")
            tf_path = Path(tf.name)

        try:
            violations = check_code_content(tf_path)
            self.assertEqual(len(violations), 0)
        finally:
            tf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
