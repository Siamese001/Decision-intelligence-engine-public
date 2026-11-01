"""
tests/test_lint_no_knowledge_popping.py

Unit tests for tools/lint_no_knowledge_popping.py verifying that blind while-loop popping
and blind slice truncation on mutations are caught.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.lint_no_knowledge_popping import check_file


class TestLintNoKnowledgePopping(unittest.TestCase):
    def test_catches_while_loop_popping(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write("""
def trim_signals(signals, max_budget):
    while len(signals) > max_budget:
        signals.pop()
    return signals
""")
            tf_path = Path(tf.name)

        try:
            violations = check_file(tf_path)
            self.assertTrue(len(violations) >= 1)
            self.assertIn("Blind while-loop popping detected", violations[0])
        finally:
            tf_path.unlink(missing_ok=True)

    def test_catches_blind_slice_truncation_on_signals(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write("""
def trim_excess(signals, excess):
    signals = signals[:-excess]
    return signals
""")
            tf_path = Path(tf.name)

        try:
            violations = check_file(tf_path)
            self.assertTrue(len(violations) >= 1)
            self.assertIn("Blind slice truncation detected", violations[0])
        finally:
            tf_path.unlink(missing_ok=True)

    def test_passes_clean_sorted_eviction(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write("""
def prioritize_and_trim(signals, max_budget):
    # Sort signals by semantic gravity rank
    ranked = sorted(signals, key=lambda s: s.get("priority", 0), reverse=True)
    return ranked[:max_budget]
""")
            tf_path = Path(tf.name)

        try:
            violations = check_file(tf_path)
            self.assertEqual(len(violations), 0)
        finally:
            tf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
