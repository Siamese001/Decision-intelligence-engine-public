"""Adversarial / edge cases for PR queue Domain classification."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.domain import classify_domain, load_taxonomy


class TestPRQueueDomainAdversarial(unittest.TestCase):
    def test_empty_and_none_inputs(self):
        self.assertEqual(classify_domain("", []), "Enhancement")
        self.assertEqual(classify_domain("feat/x", None), "Enhancement")  # type: ignore[arg-type]

    def test_missing_taxonomy_fails_visibly(self):
        self.assertEqual(classify_domain("feat/live-x", [], taxonomy={}), "—")

    def test_malformed_branch_names(self):
        for name in ("////", "feat/", "---", "FEAT/LIVE-RECORDER", "feat/live_recorder.v2"):
            with self.subTest(name=name):
                self.assertIsInstance(classify_domain(name, []), str)
        self.assertEqual(classify_domain("FEAT/LIVE-RECORDER", []), "Live Meeting")

    def test_keyword_substring_does_not_false_match(self):
        # 'pr' must match as a token, not inside 'prompt'/'price'
        self.assertEqual(classify_domain("feat/prompt-price-tweak", ["web/a.ts"]), "Enhancement")
        # conventional prefix token is not itself a keyword hit ('test/...' prefix stays fallback)
        self.assertEqual(classify_domain("test/foo", ["web/a.ts"]), "Testing")

    def test_generated_receipts_do_not_drive_domain(self):
        files = ["reports/a.json", "reports/b.json", "hitl_decision_ledger.json", "pipeline_b_extraction/x.py"]
        self.assertEqual(classify_domain("feat/misc", files), "Ingestion")

    def test_transcript_cache_is_not_a_deliverable(self):
        files = ["output/transcript_cache/a.wav", "output/transcript_cache/b.wav"]
        self.assertNotEqual(classify_domain("feat/misc", files), "Meeting Deliverable")

    def test_deliverable_minority_does_not_win(self):
        files = ["output/p/10-01-2026/s/01.md"] + [f"dual_pipeline/audio/{i}.py" for i in range(5)]
        self.assertEqual(classify_domain("feat/misc", files), "Live Meeting")

    def test_huge_path_list_is_bounded(self):
        files = [f"tests/t_{i}.py" for i in range(50_000)]
        self.assertEqual(classify_domain("feat/misc", files), "Testing")

    def test_rubric_trace_never_leaks(self):
        label = classify_domain("feat/meeting-rerun-cost-optimization", ["dual_pipeline/cost_tracker.py"])
        self.assertNotRegex(label, r"R\d+\.\w+|LOC|P[0-3]")

    def test_custom_taxonomy_override(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tax.json"
            p.write_text(json.dumps({"domains": [{"label": "X", "name_keywords": ["zz"], "path_prefixes": []}],
                                     "default_label": "Y"}), encoding="utf-8")
            tax = load_taxonomy(str(p))
            self.assertEqual(classify_domain("feat/zz-thing", [], taxonomy=tax), "X")
            self.assertEqual(classify_domain("feat/other", [], taxonomy=tax), "Y")


if __name__ == "__main__":
    unittest.main()
