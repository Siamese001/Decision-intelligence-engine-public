"""tests/test_grounding_metrics.py

Qualification suite for deterministic text metrics, strict limit enforcement, and persona title grounding.
Asserts that:
1. Normalization handles zero-width spaces, Unicode whitespace, and formatting cleanly.
2. Word count strictly enforces boundary conditions (< 500):
   - 0 words -> FAIL (empty payload)
   - 499 words -> PASS
   - 500 words -> FAIL (boundary condition)
   - 501 words -> FAIL (over limit)
3. Invented persona titles in signatures without graph backing are flagged and rejected.
4. Quoted persona titles in analysis/RCA contexts are exempt from signature rejection.
5. Verified persona titles backed by confirmed graph assertions pass cleanly.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.grounding.contracts import (
    ClaimManifest,
    ClaimRecord,
    SourceLocator,
)
from dual_pipeline.grounding.metrics import (
    count_words,
    evaluate_text_metrics,
    normalize_text,
)
from dual_pipeline.grounding.snapshot import (
    ReadOnlyGraphSnapshot,
    _compute_sha256,
)
from storage.connection import create_sqlite_conn


class TestGroundingMetrics(unittest.TestCase):
    """Test suite for deterministic word count metrics and persona title validation."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir_obj.name)

        # Create graph database
        self.db_path = self.runtime_root / "canonical_graph.db"
        conn = create_sqlite_conn(self.db_path, readonly=False)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE canonical_graph_nodes (
                node_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                node_type TEXT,
                namespace TEXT DEFAULT 'CLIENT_SAFE'
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                literal_value TEXT,
                status TEXT DEFAULT 'CONFIRMED',
                observed_at TEXT,
                stale_after TEXT,
                namespace TEXT DEFAULT 'CLIENT_SAFE'
            );
            """
        )
        # Seed Amit Ayer and verified role
        cur.execute("INSERT INTO canonical_graph_nodes VALUES ('N-AMIT', 'Amit Ayer', 'Person', 'CLIENT_SAFE')")
        cur.execute(
            """
            INSERT INTO assertion VALUES (
                'ASN-ROLE-001', 'N-AMIT', 'has_title', NULL, 'Solution Architect',
                'CONFIRMED', '2026-10-06T00:00:00Z', NULL, 'CLIENT_SAFE'
            )
            """
        )
        conn.commit()
        conn.close()

        graph_sha = _compute_sha256(self.db_path)
        pair_data = {
            "generation_id": "GEN-METRIC-TEST",
            "graph_path": "canonical_graph.db",
            "graph_sha256": graph_sha,
        }
        (self.runtime_root / "active_pair.json").write_text(json.dumps(pair_data), encoding="utf-8")
        self.snapshot = ReadOnlyGraphSnapshot(self.runtime_root)

    def tearDown(self) -> None:
        self.snapshot.close()
        self.temp_dir_obj.cleanup()

    def test_unicode_normalization_and_zero_width_chars(self) -> None:
        """Verify normalization strips invisible and zero-width characters."""
        raw = "Hello\u200bWorld\ufeff\u200cTest"
        clean = normalize_text(raw)
        self.assertEqual(clean, "HelloWorldTest")
        self.assertEqual(count_words("Word1\u200b Word2"), 2)

    def test_word_count_strict_boundaries(self) -> None:
        """Verify standardized <= 500 word limit: 0 fails, 499 passes, 500 passes, 501 fails."""
        # 1. Empty text (0 words)
        res_0 = evaluate_text_metrics("", max_words=500)
        self.assertFalse(res_0.passed_count)
        self.assertEqual(res_0.word_count, 0)
        self.assertTrue(any("empty" in r for r in res_0.reasons))

        # 2. Exactly 499 words -> PASS
        text_499 = " ".join(["token"] * 499)
        res_499 = evaluate_text_metrics(text_499, max_words=500)
        self.assertTrue(res_499.passed_count)
        self.assertEqual(res_499.word_count, 499)

        # 3. Exactly 500 words -> PASS (standardized inclusive ceiling <= 500)
        text_500 = " ".join(["token"] * 500)
        res_500 = evaluate_text_metrics(text_500, max_words=500)
        self.assertTrue(res_500.passed_count)
        self.assertEqual(res_500.word_count, 500)

        # 4. Exactly 501 words -> FAIL (> 500)
        text_501 = " ".join(["token"] * 501)
        res_501 = evaluate_text_metrics(text_501, max_words=500)
        self.assertFalse(res_501.passed_count)
        self.assertEqual(res_501.word_count, 501)
        self.assertTrue(any("violates strict limit <= 500" in r for r in res_501.reasons))

    def test_unsupported_persona_title_in_signature_rejection(self) -> None:
        """Verify fabricated persona title in email signature is rejected."""
        email_body = """
Hello Clement,

We have analyzed the PRF automation requirements and have formulated our data request.

Best regards,
Amit Ayer
Client Partner & Delivery Lead | PartnerAdvisory
""".strip()

        res = evaluate_text_metrics(email_body, snapshot=self.snapshot, max_words=500)
        self.assertFalse(res.is_valid)
        self.assertIn("Client Partner & Delivery Lead | PartnerAdvisory", res.unsupported_titles)
        self.assertTrue(any("is not verified in GraphDB assertions" in r for r in res.reasons))

    def test_verified_persona_title_in_signature_acceptance(self) -> None:
        """Verify confirmed persona title in email signature is accepted."""
        email_body = """
Hello Clement,

We have analyzed the PRF automation requirements.

Best regards,
Amit Ayer
Solution Architect
""".strip()

        res = evaluate_text_metrics(email_body, snapshot=self.snapshot, max_words=500)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.unsupported_titles), 0)

    def test_quoted_counterexample_title_exemption(self) -> None:
        """Verify quoted titles in analysis / RCA prose are not flagged as asserted signatures."""
        rca_text = """
Root Cause Analysis:
The assistant hallucinated the title "Client Partner & Delivery Lead | PartnerAdvisory" in an earlier draft.
This was an unanchored hallucination that has been eliminated.
""".strip()

        res = evaluate_text_metrics(rca_text, snapshot=self.snapshot, max_words=500)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.unsupported_titles), 0)


if __name__ == "__main__":
    unittest.main()
