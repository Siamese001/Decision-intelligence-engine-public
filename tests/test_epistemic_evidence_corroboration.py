"""Unit tests for epistemic evidence corroboration and dynamic fact promotion."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import (
    digest,
    exact_file_copy,
    seal_artifact,
    sha256_file,
    stable_id,
    utc_now,
)
from dual_pipeline.intake import (
    _route_for_candidate,
)
from dual_pipeline.release import GovernedGraphWriter
from dual_pipeline.signal_extraction import (
    FACT_PROMOTION_THRESHOLD,
    confidence_score_to_label,
    recalculate_assertion_credibility,
    source_authority_weight,
)

ROOT = Path(__file__).resolve().parent.parent


class TestEpistemicEvidenceCorroboration(unittest.TestCase):
    """Test suite verifying epistemic credibility scoring and promotion lifecycle."""

    def test_01_source_authority_weights(self) -> None:
        """Verify authority ranks 1-9 map to monotonic epistemic authority weights."""
        self.assertEqual(0.95, source_authority_weight(1))
        self.assertEqual(0.85, source_authority_weight(2))
        self.assertEqual(0.75, source_authority_weight(3))
        self.assertEqual(0.60, source_authority_weight(4))
        self.assertEqual(0.45, source_authority_weight(5))
        self.assertEqual(0.35, source_authority_weight(6))
        self.assertEqual(0.25, source_authority_weight(7))
        self.assertEqual(0.15, source_authority_weight(8))
        self.assertEqual(0.10, source_authority_weight(9))
        # Default fallback for None and clamped bounds
        self.assertEqual(0.50, source_authority_weight(None))
        self.assertEqual(0.95, source_authority_weight(0))
        self.assertEqual(0.10, source_authority_weight(10))

    def test_02_confidence_score_to_controlled_vocabulary_label(self) -> None:
        """Verify continuous confidence scores map to controlled vocabulary tiers."""
        self.assertEqual("confirmed", confidence_score_to_label(0.98))
        self.assertEqual("confirmed", confidence_score_to_label(0.95))
        self.assertEqual("high", confidence_score_to_label(0.94))
        self.assertEqual("high", confidence_score_to_label(0.85))
        self.assertEqual("medium-high", confidence_score_to_label(0.84))
        self.assertEqual("medium-high", confidence_score_to_label(0.70))
        self.assertEqual("medium", confidence_score_to_label(0.69))
        self.assertEqual("medium", confidence_score_to_label(0.50))
        self.assertEqual("low", confidence_score_to_label(0.49))
        self.assertEqual("low", confidence_score_to_label(0.10))

    def test_03_bayesian_recalculate_assertion_credibility_promotes_with_independent_corroboration(
        self,
    ) -> None:
        """Verify Bayesian accumulation calculates increased score and qualifies promotion."""
        # Baseline candidate assertion: prior score 0.65, 1 existing source
        cred = recalculate_assertion_credibility(
            prior_score=0.65,
            new_evidence=[{"extraction_confidence": 0.85}],
            authority_rank=2,  # weight 0.85
            prior_corroborator_count=1,
            is_independent=True,
        )

        # Expected:
        # effective_support = 0.85 * 0.85 = 0.7225
        # discount = 1 - 0.7225 = 0.2775
        # updated = 1 - (1 - 0.65) * 0.2775 = 1 - 0.35 * 0.2775 = 1 - 0.097125 = 0.902875 -> 0.9029
        self.assertAlmostEqual(0.9029, cred["updated_score"], places=4)
        self.assertEqual("high", cred["confidence_label"])
        self.assertEqual(2, cred["independent_corroborator_count"])
        self.assertTrue(cred["promotion_eligible"])

    def test_04_bayesian_recalculate_rejects_single_source_self_corroboration(self) -> None:
        """Verify additional evidence from the SAME source cannot satisfy the multi-source rule."""
        cred = recalculate_assertion_credibility(
            prior_score=0.65,
            new_evidence=[0.95],
            authority_rank=1,
            prior_corroborator_count=1,
            is_independent=False,  # Same source / author
        )

        # Score increases due to additional evidence
        self.assertGreater(cred["updated_score"], 0.85)
        # But corroborator count remains 1
        self.assertEqual(1, cred["independent_corroborator_count"])
        # Promotion MUST be blocked without independent corroboration
        self.assertFalse(cred["promotion_eligible"])

    def test_05_bayesian_recalculate_weak_evidence_does_not_promote(self) -> None:
        """Verify low-authority or low-confidence evidence does not breach threshold."""
        cred = recalculate_assertion_credibility(
            prior_score=0.50,
            new_evidence=[0.40],
            authority_rank=8,  # weight 0.15
            prior_corroborator_count=1,
            is_independent=True,
        )
        self.assertLess(cred["updated_score"], FACT_PROMOTION_THRESHOLD)
        self.assertEqual(2, cred["independent_corroborator_count"])
        self.assertFalse(cred["promotion_eligible"])

    def test_06_route_for_candidate_detects_corroborating_evidence(self) -> None:
        """Verify duplicate text from a different source is routed to CORROBORATING_EVIDENCE."""
        candidate = {
            "candidate_id": "cand-test-01",
            "candidate_type": "source_chunk",
            "source_id": "src-meeting-beta",
            "content": "Architecture conforms to standard specifications.",
            "evidence_ids": ["evi-beta-01"],
            "classification": "internal",
            "audiences": ["internal"],
        }
        candidate_comparison = {
            "exact_matches": [
                {
                    "object_type": "search_document",
                    "object_id": "asrt-prior-alpha",
                    "source_id": "src-meeting-alpha",  # Different source!
                }
            ],
            "entity_matches": [],
        }

        route, reasons = _route_for_candidate(
            candidate,
            [],
            candidate_comparison,
        )

        self.assertEqual("CORROBORATING_EVIDENCE", route)
        self.assertTrue(any("corroborating evidence" in r for r in reasons))

        # In contrast, an exact match from the SAME source without new evidence routes to EXACT_DUPLICATE
        same_source_comp = {
            "exact_matches": [
                {
                    "object_type": "search_document",
                    "object_id": "asrt-prior-alpha",
                    "source_id": "src-meeting-beta",  # Same source
                }
            ],
            "entity_matches": [],
        }
        route_dup, reasons_dup = _route_for_candidate(
            candidate,
            [],
            same_source_comp,
        )
        self.assertEqual("EXACT_DUPLICATE", route_dup)

    def test_07_governed_graph_writer_executes_add_evidence_and_promotes(self) -> None:
        """Test full transactional apply of add_evidence and fact promotion to canonical."""
        seed_graph = ROOT / "graph" / "nee_graph.sqlite"
        self.assertTrue(seed_graph.is_file(), f"seed graph missing at {seed_graph}")

        with tempfile.TemporaryDirectory(prefix="nee-corroboration-test-") as temp_dir:
            graph_copy = Path(temp_dir) / "graph.sqlite"
            exact_file_copy(seed_graph, graph_copy)

            utc_now()
            run_id = stable_id("run", "test-corroboration-run", length=28)
            change_set_id = stable_id("change-set", run_id, length=28)
            approval_id = "govapp_v50_partner_advisory_pdlc_strategic_partner_001"

            # 1. Use real governed candidate assertion from the seed graph
            asrt_id = "asrt_claim_claim_v21_sherpa_implementation_transition"

            conn = sqlite3.connect(graph_copy)
            try:
                conn.execute(
                    """UPDATE assertion
                       SET canonical_status='candidate', confidence_score=0.65, confidence_label='medium',
                           canonical_eligible=0, retrieval_eligible=0
                       WHERE assertion_id=?""",
                    (asrt_id,),
                )
                conn.execute(
                    """UPDATE search_document
                       SET canonical_status='candidate', retrieval_eligible=0
                       WHERE assertion_id=?""",
                    (asrt_id,),
                )
                conn.commit()
            finally:
                conn.close()

            # 2. Prepare change set with new corroborating evidence from source Beta (authority_rank 2)
            src_beta_id = "src-meeting-beta"
            src_beta_ver = "src-ver-beta-1"
            evi_beta_id = "evi-beta-001"
            op_id = stable_id("graph-operation", run_id, "add-evidence-01", length=28)

            source_payload = {
                "source_id": src_beta_id,
                "source_version_id": src_beta_ver,
                "name": "Meeting Beta",
                "authority_rank": 2,
                "source_posture": "derivative_internal",
                "source_location": "docs/beta.md",
                "package_digest": "b" * 64,
                "evidence": [
                    {
                        "evidence_id": evi_beta_id,
                        "locator": {"kind": "section", "value": "3.1"},
                        "directness": "direct_statement",
                        "authority_rank": 2,
                        "extraction_confidence": 0.90,
                    }
                ],
            }

            op_payload = {
                "candidate": {
                    "candidate_id": "cand-beta-01",
                    "candidate_type": "source_chunk",
                    "evidence_ids": [evi_beta_id],
                    "classification": "internal",
                    "audiences": ["internal"],
                },
                "disposition": "CANDIDATE",
                "assertion_id": asrt_id,
            }

            base_hash = sha256_file(graph_copy)
            change_set = {
                "contract_id": "urn:nee:dual-pipeline:graph-change-set:1.0.0",
                "schema_version": "1.0.0",
                "artifact_id": change_set_id,
                "change_set_id": change_set_id,
                "run_id": run_id,
                "trace_id": "0" * 32,
                "status": "approved",
                "base_graph_snapshot": stable_id("graph-snapshot", base_hash, length=24),
                "base_graph_hash": base_hash,
                "approval_id": approval_id,
                "operations": [
                    {
                        "operation_id": op_id,
                        "sequence": 1,
                        "operation": "add_evidence",
                        "target_type": "assertion",
                        "target_id": asrt_id,
                        "before_state_hash": "0" * 64,
                        "after_state_hash": digest(op_payload),
                        "evidence_ids": [evi_beta_id],
                        "classification": "internal",
                        "audiences": ["internal"],
                        "candidate_id": "cand-beta-01",
                    }
                ],
                "transaction_required": True,
                "rollback_required": True,
                "extensions": {
                    "com.partner_advisory.nee.source": source_payload,
                    "com.partner_advisory.nee.operation-payloads": {op_id: op_payload},
                },
            }
            sealed_change_set = seal_artifact(change_set)

            # 3. Apply change set via GovernedGraphWriter
            writer = GovernedGraphWriter(graph_copy)
            receipt = writer.apply(sealed_change_set)

            self.assertEqual("COMMITTED", receipt["status"])
            self.assertEqual(1, receipt["operation_count"])
            self.assertEqual(1, len(receipt["operations"]))
            self.assertEqual(op_id, receipt["operations"][0]["operation_id"])
            self.assertEqual(asrt_id, receipt["operations"][0]["target_id"])

            # 4. Verify post-apply state in SQLite
            conn = sqlite3.connect(graph_copy)
            conn.row_factory = sqlite3.Row
            try:
                # Evidence was linked
                ae_count = conn.execute(
                    "SELECT count(*) FROM assertion_evidence WHERE assertion_id=?", (asrt_id,)
                ).fetchone()[0]
                self.assertEqual(2, ae_count)

                # Assertion was promoted
                asrt_row = dict(
                    conn.execute("SELECT * FROM assertion WHERE assertion_id=?", (asrt_id,)).fetchone()
                )
                self.assertEqual("canonical", asrt_row["canonical_status"])
                self.assertEqual(1, asrt_row["canonical_eligible"])
                self.assertEqual(1, asrt_row["retrieval_eligible"])
                self.assertGreaterEqual(asrt_row["confidence_score"], 0.85)
                self.assertEqual("high", asrt_row["confidence_label"])

                # Search document was synchronized
                doc_row = dict(
                    conn.execute("SELECT * FROM search_document WHERE assertion_id=?", (asrt_id,)).fetchone()
                )
                self.assertEqual("canonical", doc_row["canonical_status"])
                self.assertEqual(1, doc_row["retrieval_eligible"])

                # Audit event was recorded
                audit_row = dict(
                    conn.execute(
                        "SELECT * FROM audit_event WHERE record_id=? ORDER BY event_timestamp DESC",
                        (asrt_id,),
                    ).fetchone()
                )
                self.assertEqual("corroborate_and_promote", audit_row["action"])
                new_vals = json.loads(audit_row["new_value_json"])
                self.assertTrue(new_vals.get("promoted"))
                self.assertEqual("canonical", new_vals.get("canonical_status"))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
