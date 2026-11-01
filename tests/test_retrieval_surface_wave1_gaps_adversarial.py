"""Adversarial & Edge-Case Tests for Wave 1 Retrieval Surface Functionality Gap Remediation."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from dual_pipeline.agent_sdk.tools.retrieval_tools import evaluate_candidate_retrieval
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.ptc.hybrid_query import (
    PTCHybridQueryExecutor,
    PTCHybridQueryPlan,
    RetrievedFact,
)
from dual_pipeline.query_core.coverage import build_retrieval_evidence_report
from dual_pipeline.query_core.hydration import _effective_requested_audiences
from dual_pipeline.retrieval.agents.release_gate_auditor import ReleaseGateAuditor
from dual_pipeline.retrieval.agents.retrieval_coordinator import RetrievalCoordinator


class Wave1RetrievalGapsAdversarialTests(unittest.TestCase):
    def test_exact_production_regression_cand_review_with_zero_candidates_vs_unauthorized_lane(self) -> None:
        """Category 1 (exact_production_regression): candidate_promotion_review excludes empty candidate lane only when candidate lane is authorized and approved evidence exists."""
        manifest = {
            "run_id": "run-adv-cand",
            "validated_request_digest": "1" * 64,
            "query_plan_digest": "2" * 64,
            "canonical_digest": {"value": "3" * 64},
            "requirements": [
                {
                    "requirement_id": "req-cand-only",
                    "requirement_label": "Candidate assertions under review",
                    "requirement_kind": "evidence",
                    "allowed_lanes": ["candidate"],
                    "missing_evidence_disposition": "explicit_gap",
                    "materiality": "high",
                }
            ],
        }
        # When approved records exist and candidate lane is authorized but empty -> excluded
        pkt_with_approved = {
            "graph_snapshot": "snap-1",
            "packet_digest": "4" * 64,
            "allowed_lanes": ["approved", "candidate"],
            "approved": [
                {
                    "record": {
                        "record_id": "rec-app-1",
                        "title": "Approved fact",
                        "retrieval_text": "Corroborated approved fact",
                        "evidence_ids": ["ev-1"],
                    },
                    "evidence": [{"evidence_id": "ev-1"}],
                }
            ],
            "candidate": [],
            "external_public": [],
        }
        rep_ok = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet=pkt_with_approved,
            authorization_scope_digest="5" * 64,
            graph_sha256="6" * 64,
            view_authorized=True,
        )
        self.assertEqual("excluded", rep_ok["requirement_outcomes"][0]["outcome"])

        # When BOTH approved and candidate lanes are empty -> must still fail closed as explicit_gap!
        pkt_all_empty = {
            "graph_snapshot": "snap-1",
            "packet_digest": "4" * 64,
            "allowed_lanes": ["approved", "candidate"],
            "approved": [],
            "candidate": [],
            "external_public": [],
        }
        rep_empty = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet=pkt_all_empty,
            authorization_scope_digest="5" * 64,
            graph_sha256="6" * 64,
            view_authorized=True,
        )
        self.assertEqual("explicit_gap", rep_empty["requirement_outcomes"][0]["outcome"])

    def test_invariant_bypass_attempt_public_audience_and_forged_candidate_gates(self) -> None:
        """Category 2 (invariant_bypass_attempt): public classification cannot expand internal_consulting, and forged mock/leakage candidate flags fail 11-gate check."""
        self.assertEqual(
            {"nee-engineering"},
            _effective_requested_audiences(["nee-engineering"], classification="public"),
        )
        self.assertEqual(
            {"nee-engineering", "internal_consulting"},
            _effective_requested_audiences(["nee-engineering"], classification="internal"),
        )

        # Forged candidate with forbidden leakage or mock backend must fail 11 gates
        leaking_cand = [
            {
                "id": "c-leak",
                "score": 0.99,
                "forbidden_leakage": True,
                "real_bge_m3": False,
                "pair_binding": False,
                "delta_thresholds_passed": False,
                "rank_stability": False,
                "repeat_stability": False,
                "component_execution": False,
            }
        ]
        res = evaluate_candidate_retrieval("query", candidates=leaking_cand)
        self.assertFalse(res["passed_11_gates"])
        self.assertEqual("REJECTED", res["status"])
        for gate in (
            "real_bge_m3",
            "pair_binding",
            "delta_thresholds",
            "rank_stability",
            "repeat_stability",
            "forbidden_leakage",
            "component_execution",
        ):
            self.assertEqual("FAIL", res["gate_verdicts"][gate])

    def test_boundary_and_malformed_inputs_across_backends_ptc_and_coordinator(self) -> None:
        """Category 3 (boundary_and_malformed_input): canonical backend permission guards, RetrievedFact objects in PTCHybridQueryExecutor, and negative delta in strict coordinator audit."""
        with self.assertRaises(RuntimeError):
            get_backend("flagembedding-bge-m3-dev", allow_dev_backend=False)
        with self.assertRaises(RuntimeError):
            get_backend("flagembedding-bge-m3-internal", allow_internal_backend=False)
        with self.assertRaises(ValueError):
            get_backend("flagembedding-unknown-model")

        with patch("dual_pipeline.index_core.backends.FlagEmbeddingBackend", return_value="prod-sentinel"), patch(
            "dual_pipeline.index_core.backends.InternalOperationalFlagEmbeddingBackend", return_value="int-sentinel"
        ):
            self.assertEqual("prod-sentinel", get_backend("flagembedding-bge-m3"))
            self.assertEqual(
                "int-sentinel",
                get_backend("flagembedding-bge-m3-internal", allow_internal_backend=True),
            )

        # RetrievedFact objects in PTCHybridQueryExecutor + cyclic/missing hop edges
        class _FactObjEngine:
            def search(
                self,
                query: str,
                *,
                stealth_mode: bool = False,
                min_strategic_value: float = 0.0,
                include_quarantined: bool = False,
                limit: int = 10,
            ) -> list[RetrievedFact]:
                return [
                    RetrievedFact(
                        node_id="f1",
                        evidence_id="ev1",
                        namespace="CLIENT_SAFE",
                        type="entity",
                        name="S1",
                        snippet="O1",
                        strategic_value=0.9,
                        confidence=0.9,
                        tacticality=0.8,
                        is_quarantined=False,
                        relevance_score=0.9,
                        properties={"role": "fact"},
                    ),
                    RetrievedFact(
                        node_id="f2",
                        evidence_id="ev2",
                        namespace="CLIENT_SAFE",
                        type="entity",
                        name="S2",
                        snippet="O2",
                        strategic_value=0.8,
                        confidence=0.8,
                        tacticality=0.8,
                        is_quarantined=False,
                        relevance_score=0.8,
                        properties={"role": "risk"},
                        connected_edges=[
                            {"from_node_id": "f2", "to_node_id": "f2", "relationship": "SELF_LOOP", "hop": 1},
                            {"from_node_id": "f2", "to_node_id": "T1", "relationship": "VALID", "hop": 1},
                            {"from_node_id": "T1", "to_node_id": "T2", "relationship": "TOO_DEEP", "hop": 5},
                        ],
                    ),
                ]

        exec_obj = PTCHybridQueryExecutor(retrieval_engine=_FactObjEngine())
        summary = exec_obj.execute(
            PTCHybridQueryPlan(
                query="risk check",
                limit=1,
                allowed_roles=["risk"],
                expand_subgraph=True,
                max_subgraph_depth=1,
            )
        )
        self.assertEqual(1, summary.total_matches)
        self.assertEqual("f2", summary.top_facts[0]["node_id"])
        subgraph_node_ids = {n["node_id"] for n in summary.subgraph_nodes}
        self.assertIn("T1", subgraph_node_ids)
        self.assertNotIn("T2", subgraph_node_ids)

        # Coordinator strict audit with negative delta fails delta_thresholds
        import asyncio
        from dual_pipeline.a2a import A2ATask, A2ATaskStatus

        coord = RetrievalCoordinator(
            mock_mode=True,
            auditor_agent=ReleaseGateAuditor(mock_mode=True, strict_payload=True),
        )
        neg_task = A2ATask(
            task_id="task-neg-delta",
            parent_task_id=None,
            sender_agent_id="urn:nee:agent:caller",
            recipient_agent_id=coord.card.agent_id,
            objective="Test negative delta rejection",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "query": "test negative delta",
                "candidate_facts": [
                    {
                        "node_id": "f1",
                        "name": "Fact 1",
                        "content": "test negative delta fact",
                        "confidence": 0.9,
                        "classification": "CLIENT_SAFE",
                        "is_quarantined": 0,
                    }
                ],
                "has_delta": True,
                "delta_recall": -0.05,
                "delta_mrr": 0.0,
                "delta_ndcg": 0.0,
            },
        )
        neg_delta_res = asyncio.run(coord.handle_task(neg_task))
        self.assertFalse(neg_delta_res["all_gates_passed"])
        self.assertEqual("BLOCKED", neg_delta_res["qualification_status"])

    def test_concurrency_or_state_isolation_across_parallel_coordinator_and_ptc_queries(self) -> None:
        """Category 4 (concurrency_or_state_isolation): concurrent RetrievalCoordinator and PTCHybridQueryExecutor calls maintain isolated perspective and role state."""
        errors: list[Exception] = []

        def _worker(idx: int) -> None:
            try:
                is_comm = idx % 2 == 0
                query = "commercial milestone SOW budget" if is_comm else "technical architecture latency"
                coord = RetrievalCoordinator(mock_mode=True)
                out = coord.retrieve(
                    query=query,
                    candidate_facts=[
                        {
                            "node_id": f"f_{idx}",
                            "name": f"Fact {idx}",
                            "content": query,
                            "confidence": 0.9,
                            "classification": "CLIENT_SAFE",
                            "is_quarantined": 0,
                        }
                    ],
                )
                assert out["ranked_results"][0]["node_id"] == f"f_{idx}"
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
