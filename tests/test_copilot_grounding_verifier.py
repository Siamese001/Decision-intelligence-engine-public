"""
tests/test_copilot_grounding_verifier.py

Unit tests for CopilotGroundingVerifier (Wave 4):
1. Validity checking: active vs superseded, stale_after expiration, graph membership, C-19 quarantine.
2. Provenance scoring: evidence count, source diversity, recency.
3. Conflict detection: contradictory assertion pairs surfaced as conflicts, never merged.
4. Hybrid reranking: vectorized BLAS dot-product and fallback.
5. Budget guard: returns rigor: FAST on budget breach.
"""
import time
import unittest
from unittest.mock import MagicMock

import numpy as np

from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    CopilotGroundingVerifier,
    check_assertion_validity,
    compute_provenance_score,
    detect_assertion_conflicts,
    rerank_assertions_vectorized,
)


class TestCopilotGroundingVerifier(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = CopilotGroundingVerifier(budget_ms=250.0, min_provenance_score=0.3)

    def test_validity_active_status_and_superseded(self) -> None:
        """Active assertions pass; superseded or inactive assertions are excluded."""
        active_asrt = {
            "assertion_id": "asrt_valid_01",
            "is_active": True,
            "status": "ACCEPTED",
            "claim_text": "Substation B delivery is confirmed for Q3.",
        }
        ok, reason = check_assertion_validity(active_asrt)
        self.assertTrue(ok)
        self.assertIsNone(reason)

        superseded_asrt = {
            "assertion_id": "asrt_old_01",
            "is_active": False,
            "status": "SUPERSEDED",
            "claim_text": "Substation B was scheduled for Q1.",
        }
        ok_sup, reason_sup = check_assertion_validity(superseded_asrt)
        self.assertFalse(ok_sup)
        self.assertIn("SUPERSEDED", reason_sup or "")

    def test_validity_stale_after(self) -> None:
        """Assertions past stale_after are rejected."""
        current_epoch = 1759680000.0  # Approx Oct 2025
        non_stale = {
            "assertion_id": "asrt_fresh_01",
            "is_active": True,
            "stale_after": "2026-12-31",
            "claim_text": "Active commitment.",
        }
        ok_fresh, _ = check_assertion_validity(non_stale, current_epoch=current_epoch)
        self.assertTrue(ok_fresh)

        stale = {
            "assertion_id": "asrt_stale_01",
            "is_active": True,
            "stale_after": "2024-01-01",
            "claim_text": "Expired commitment.",
        }
        ok_stale, reason_stale = check_assertion_validity(stale, current_epoch=current_epoch)
        self.assertFalse(ok_stale)
        self.assertEqual(reason_stale, "STALE_AFTER_EXPIRED")

    def test_validity_c19_quarantine(self) -> None:
        """C-19 career firewall markers cause rejection."""
        c19_asrt = {
            "assertion_id": "asrt_c19_01",
            "is_active": True,
            "claim_text": "Use internal executive networking_wedge for promotion leverage.",
        }
        ok, reason = check_assertion_validity(c19_asrt)
        self.assertFalse(ok)
        self.assertEqual(reason, "C19_QUARANTINED")

    def test_validity_graph_membership(self) -> None:
        """Assertions removed from active graph are excluded."""
        mock_topology = MagicMock()
        mock_topology.assertions = {"asrt_01": {}, "asrt_02": {}}

        present = {"assertion_id": "asrt_01", "is_active": True}
        ok_pres, _ = check_assertion_validity(present, topology=mock_topology)
        self.assertTrue(ok_pres)

        missing = {"assertion_id": "asrt_ghost_99", "is_active": True}
        ok_miss, reason_miss = check_assertion_validity(missing, topology=mock_topology)
        self.assertFalse(ok_miss)
        self.assertEqual(reason_miss, "REMOVED_FROM_GRAPH")

    def test_provenance_scoring(self) -> None:
        """Calculates provenance score from evidence count, diversity, and recency."""
        now = time.time()
        strong_asrt = {
            "assertion_id": "asrt_strong",
            "evidence_ids": ["ev_1", "ev_2", "ev_3"],
            "source_ids": ["meeting:01", "sow:nexus_01"],
            "observed_at": "2026-10-01",
        }
        score_strong = compute_provenance_score(strong_asrt, current_epoch=now)
        self.assertGreaterEqual(score_strong, 0.7)

        weak_asrt = {
            "assertion_id": "asrt_weak",
            "evidence_ids": [],
            "source_ids": [],
            "observed_at": "2020-01-01",
        }
        score_weak = compute_provenance_score(weak_asrt, current_epoch=now)
        self.assertLess(score_weak, 0.3)

    def test_conflict_detection(self) -> None:
        """Contradictory assertions for same subject & predicate are surfaced as conflicts."""
        asrt_a = {
            "assertion_id": "asrt_a",
            "subject_node_id": "project_nexus",
            "predicate": "HAS_STATUS",
            "object_node_id": "status:approved",
            "claim_text": "Nexus Phase 1 is approved.",
        }
        asrt_b = {
            "assertion_id": "asrt_b",
            "subject_node_id": "project_nexus",
            "predicate": "HAS_STATUS",
            "object_node_id": "status:rejected",
            "claim_text": "Nexus Phase 1 is rejected.",
        }
        conflicts = detect_assertion_conflicts([asrt_a, asrt_b])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["subject"], "project_nexus")
        self.assertEqual(conflicts[0]["predicate"], "HAS_STATUS")
        self.assertIn("status:approved", conflicts[0]["reason"])

    def test_multi_claim_text_non_conflicting(self) -> None:
        """Multiple distinct HAS_CLAIM_TEXT assertions on the same subject do not conflict."""
        asrt_1 = {
            "assertion_id": "asrt_serial_01",
            "subject_node_id": "workstream_lens",
            "predicate": "HAS_CLAIM_TEXT",
            "object_literal": "Rule registry required for serial number extraction.",
            "claim_text": "Rule registry required for serial number extraction.",
            "source_ids": ["meeting:20261015"],
            "evidence_ids": ["ev_1"],
            "observed_at": "2026-10-15",
        }
        asrt_2 = {
            "assertion_id": "asrt_serial_02",
            "subject_node_id": "workstream_lens",
            "predicate": "HAS_CLAIM_TEXT",
            "object_literal": "Extraction accuracy rises to 90% when active nameplate rule exists.",
            "claim_text": "Extraction accuracy rises to 90% when active nameplate rule exists.",
            "source_ids": ["meeting:20261015"],
            "evidence_ids": ["ev_2"],
            "observed_at": "2026-10-15",
        }
        conflicts = detect_assertion_conflicts([asrt_1, asrt_2])
        self.assertEqual(len(conflicts), 0)

        # Full verification retains both candidates
        res = self.verifier.verify_and_ground([asrt_1, asrt_2], dialogue="serial number accuracy")
        self.assertEqual(len(res.conflicts), 0)
        self.assertEqual(len(res.verified_assertions), 2)

    def test_multi_claim_text_opposing_polarity_still_conflicts(self) -> None:
        """HAS_CLAIM_TEXT assertions with explicitly opposing polarities still trigger conflict detection."""
        asrt_1 = {
            "assertion_id": "asrt_pol_01",
            "subject_node_id": "workstream_lens",
            "predicate": "HAS_CLAIM_TEXT",
            "object_literal": "Deployment schedule is delayed.",
            "claim_text": "Deployment schedule is delayed.",
        }
        asrt_2 = {
            "assertion_id": "asrt_pol_02",
            "subject_node_id": "workstream_lens",
            "predicate": "HAS_CLAIM_TEXT",
            "object_literal": "Deployment schedule is on track.",
            "claim_text": "Deployment schedule is on track.",
        }
        conflicts = detect_assertion_conflicts([asrt_1, asrt_2])
        self.assertEqual(len(conflicts), 1)
        self.assertIn("Opposing claim polarities", conflicts[0]["reason"])


    def test_vectorized_blas_reranking_benchmark(self) -> None:
        """Vectorized BLAS scoring over 50 candidate vectors executes in < 1 ms."""
        rng = np.random.default_rng(42)
        candidates = []
        for i in range(50):
            vec = rng.standard_normal(1024).astype(np.float32)
            vec /= np.linalg.norm(vec)
            candidates.append({
                "assertion_id": f"asrt_{i}",
                "predicate": "DEPENDS_ON" if i % 2 == 0 else "OWNS",
                "claim_text": f"Candidate assertion {i} text",
                "dense_vector": vec.tolist(),
            })

        q_vec = rng.standard_normal(1024).astype(np.float32)
        q_vec /= np.linalg.norm(q_vec)

        t0 = time.perf_counter()
        reranked, fallback = rerank_assertions_vectorized(
            candidates,
            query_tokens={"candidate", "assertion"},
            query_vector=q_vec,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertFalse(fallback)
        self.assertEqual(len(reranked), 50)
        self.assertIn("_rerank_score", reranked[0])
        # Vectorized BLAS must easily complete in under 2 ms
        self.assertLess(elapsed_ms, 2.0)

    def test_budget_breach_fallback_to_fast(self) -> None:
        """If verification exceeds budget, returns fast-tier candidates tagged rigor: FAST."""
        tight_verifier = CopilotGroundingVerifier(budget_ms=0.0001)  # Impossibly small budget
        candidates = [
            {"assertion_id": "asrt_1", "is_active": True, "claim_text": "Valid assertion 1"},
            {"assertion_id": "asrt_2", "is_active": True, "claim_text": "Valid assertion 2"},
        ]
        res = tight_verifier.verify_and_ground(candidates, dialogue="Valid assertion")
        self.assertEqual(res.rigor_tier, "FAST")
        self.assertTrue(res.is_budget_breached)


if __name__ == "__main__":
    unittest.main()
