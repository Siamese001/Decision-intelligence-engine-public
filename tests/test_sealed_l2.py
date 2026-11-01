"""Unit tests for SealedL2Artifact — tamper-evident L2 output seal."""
from __future__ import annotations

import hashlib
import json
import unittest

from dual_pipeline.sealed_l2 import seal_l2_output


class TestSealedL2Output(unittest.TestCase):
    """Tests for seal_l2_output."""

    def test_sealed_artifact_has_canonical_digest(self) -> None:
        sealed = seal_l2_output(
            run_id="run-001",
            query_id="query-001",
            l2_decision_artifact_id="l2d-001",
            l2_disposition="primary",
            l2_model="gpt-5.6-terra",
            l2_answer={"answer": "test answer"},
            l2_provider_call_count=1,
        )
        self.assertIn("canonical_digest", sealed)
        self.assertEqual(sealed["canonical_digest"]["algorithm"], "sha256")
        self.assertEqual(
            sealed["canonical_digest"]["scope"],
            "artifact_without_canonical_digest",
        )
        self.assertRegex(sealed["canonical_digest"]["value"], r"^[a-f0-9]{64}$")

    def test_digest_changes_with_answer(self) -> None:
        sealed_a = seal_l2_output(
            run_id="run-001",
            query_id="query-001",
            l2_decision_artifact_id="l2d-001",
            l2_disposition="primary",
            l2_model="gpt-5.6-terra",
            l2_answer={"answer": "answer A"},
            l2_provider_call_count=1,
        )
        sealed_b = seal_l2_output(
            run_id="run-001",
            query_id="query-001",
            l2_decision_artifact_id="l2d-001",
            l2_disposition="primary",
            l2_model="gpt-5.6-terra",
            l2_answer={"answer": "answer B"},
            l2_provider_call_count=1,
        )
        self.assertNotEqual(
            sealed_a["answer_digest"],
            sealed_b["answer_digest"],
        )

    def test_none_answer_produces_null_digest(self) -> None:
        sealed = seal_l2_output(
            run_id="run-001",
            query_id="query-001",
            l2_decision_artifact_id="l2d-001",
            l2_disposition="failed",
            l2_model=None,
            l2_answer=None,
            l2_provider_call_count=0,
        )
        self.assertIsNone(sealed["answer_digest"])
        self.assertEqual(sealed["answer_length_chars"], 0)

    def test_sealed_artifact_has_required_fields(self) -> None:
        sealed = seal_l2_output(
            run_id="run-001",
            query_id="query-001",
            l2_decision_artifact_id="l2d-001",
            l2_disposition="primary",
            l2_model="gpt-5.6-terra",
            l2_answer={"answer": "test"},
            l2_provider_call_count=1,
        )
        required = {
            "artifact_id",
            "run_id",
            "query_id",
            "l2_decision_artifact_id",
            "l2_disposition",
            "l2_model",
            "l2_provider_call_count",
            "l2_token_usage",
            "sealed_at",
            "answer_digest",
            "answer_length_chars",
            "canonical_digest",
        }
        self.assertTrue(required.issubset(sealed.keys()))

    def test_artifact_id_is_unique_for_different_runs(self) -> None:
        ids = set()
        for i in range(10):
            sealed = seal_l2_output(
                run_id=f"run-{i:03d}",
                query_id="query-001",
                l2_decision_artifact_id="l2d-001",
                l2_disposition="primary",
                l2_model="gpt-5.6-terra",
                l2_answer={"answer": "test"},
                l2_provider_call_count=1,
            )
            ids.add(sealed["artifact_id"])
        self.assertEqual(len(ids), 10)

    def test_digest_is_verifiable(self) -> None:
        """Verify that the canonical_digest can be recomputed."""
        sealed = seal_l2_output(
            run_id="run-001",
            query_id="query-001",
            l2_decision_artifact_id="l2d-001",
            l2_disposition="primary",
            l2_model="gpt-5.6-terra",
            l2_answer={"answer": "verification test"},
            l2_provider_call_count=1,
        )
        # Recompute: exclude canonical_digest, then hash
        body = {k: v for k, v in sealed.items() if k != "canonical_digest"}
        recomputed = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(sealed["canonical_digest"]["value"], recomputed)


if __name__ == "__main__":
    unittest.main()
