"""End-to-End Tests for Wave 1 Retrieval Surface Functionality Gap Remediation (GAP-01 to GAP-06)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.agent_sdk.tools.retrieval_tools import evaluate_candidate_retrieval
from dual_pipeline.index_core.backends import (
    DevelopmentFlagEmbeddingBackend,
    FlagEmbeddingBackend,
    InternalOperationalFlagEmbeddingBackend,
    get_backend,
)
from dual_pipeline.ptc.hybrid_query import (
    PTCHybridQueryExecutor,
    PTCHybridQueryPlan,
    RetrievedFact,
)
from dual_pipeline.query_core.coverage import (
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
    coverage_complete,
)
from dual_pipeline.query_core.hydration import exact_retrieval
from dual_pipeline.query_core.planning import _resolve_evidence_requirements
from dual_pipeline.retrieval.agents.release_gate_auditor import ReleaseGateAuditor
from dual_pipeline.retrieval.agents.retrieval_coordinator import RetrievalCoordinator


class _StubRoleRetriever:
    def __init__(self, facts: list[dict[str, object]]) -> None:
        self._facts = facts

    def search(
        self,
        query: str,
        *,
        stealth_mode: bool = False,
        min_strategic_value: float = 0.0,
        include_quarantined: bool = False,
        limit: int = 10,
    ) -> list[dict[str, object]]:
        return list(self._facts[:limit])


class Wave1RetrievalGapsE2ETests(unittest.TestCase):
    def test_gap01_candidate_promotion_review_passes_coverage_when_generation_has_zero_candidates_e2e(self) -> None:
        """GAP-01 E2E: candidate_promotion_review excludes candidate-only requirements when generation has 0 candidate records."""
        active = {
            "graph_snapshot": "graph-snapshot-gap01",
            "graph_sha256": "a" * 64,
        }
        plan = {
            "run_id": "run-gap01-cand-review",
            "query_type": "candidate_promotion_review",
            "allowed_lanes": ["approved", "candidate"],
            "materiality": "high",
            "primary_entities": ["Category Brain", "Conduit Engagement"],
            "evidence_requirements": _resolve_evidence_requirements("candidate_promotion_review", "candidate_review"),
            "subquestions": ["Review the candidate hypotheses and promotion readiness for Category Brain and Conduit Engagement."],
            "canonical_digest": {"value": "b" * 64},
        }
        manifest = compile_query_coverage_manifest(
            question="Review the candidate hypotheses and promotion readiness for Category Brain and Conduit Engagement.",
            validated_request_digest="c" * 64,
            plan=plan,
            active=active,
            authorization_scope_digest="d" * 64,
        )
        packet = {
            "graph_snapshot": active["graph_snapshot"],
            "packet_digest": "e" * 64,
            "allowed_lanes": ["approved", "candidate"],
            "approved": [
                {
                    "record": {
                        "record_id": "rec-approved-cb",
                        "title": "Category Brain and Conduit Engagement governed corroboration",
                        "retrieval_text": (
                            "Category Brain and Conduit Engagement approved corroboration, contradiction, "
                            "and unresolved validation with resolvable evidence locators."
                        ),
                        "evidence_ids": ["ev-cb-1"],
                    },
                    "evidence": [{"evidence_id": "ev-cb-1"}],
                }
            ],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
        }
        report = build_retrieval_evidence_report(
            coverage_manifest=manifest,
            packet=packet,
            authorization_scope_digest="d" * 64,
            graph_sha256=active["graph_sha256"],
            view_authorized=True,
        )
        self.assertTrue(coverage_complete(report))
        outcomes_by_label = {
            req["requirement_label"]: out
            for req, out in zip(manifest["requirements"], report["requirement_outcomes"], strict=True)
        }
        self.assertEqual("excluded", outcomes_by_label["Candidate assertions under review"]["outcome"])
        self.assertEqual(
            "NO_CANDIDATE_RECORDS_IN_GENERATION",
            outcomes_by_label["Candidate assertions under review"]["reason_code"],
        )
        self.assertEqual("excluded", outcomes_by_label["Candidate assertions and proposal-only posture"]["outcome"])

    def test_gap02_coordinator_forwards_delta_metrics_and_wires_hybrid_weights_and_perspective_healer_e2e(self) -> None:
        """GAP-02 E2E: RetrievalCoordinator forwards delta metrics in strict audit, wires L1 hybrid_weights, and avoids unranked cloning."""
        import asyncio
        from dual_pipeline.a2a import A2ATask, A2ATaskStatus

        coordinator = RetrievalCoordinator(
            mock_mode=True,
            auditor_agent=ReleaseGateAuditor(mock_mode=True, strict_payload=True),
        )
        candidates = [
            {
                "node_id": "fact_comm_1",
                "name": "SOW Budget Milestone",
                "content": "Commercial SOW milestone commitment budget and delivery target date.",
                "confidence": 0.95,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
            {
                "node_id": "fact_tech_1",
                "name": "Vector Sidecar Architecture",
                "content": "Technical architecture system dependency and data contract.",
                "confidence": 0.90,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
        ]
        task = A2ATask(
            task_id="task-gap02-e2e",
            parent_task_id=None,
            sender_agent_id="urn:nee:agent:caller",
            recipient_agent_id=coordinator.card.agent_id,
            objective="Test strict delta audit and perspective healing",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "query": "LENS commercial milestone and technical architecture",
                "candidate_facts": candidates,
                "caller_namespace": "CLIENT_SAFE",
                "stealth_mode": True,
                "has_delta": True,
                "delta_recall": 1.0,
                "delta_mrr": 1.0,
                "delta_ndcg": 1.0,
            },
        )
        res = asyncio.run(coordinator.handle_task(task))
        self.assertEqual("COMPLETED", res["status"])
        self.assertTrue(res["all_gates_passed"])
        self.assertEqual("DELIVERY_READY", res["qualification_status"])
        self.assertIn("ADOPTION", res["healed_perspectives"])
        self.assertEqual({"fact_comm_1", "fact_tech_1"}, {f["node_id"] for f in res["fused_facts"]})

    def test_gap03_exact_retrieval_blocks_internal_consulting_expansion_under_public_classification_e2e(self) -> None:
        """GAP-03 E2E: exact_retrieval does not expand nee-engineering to internal_consulting when classification is public."""
        with tempfile.TemporaryDirectory(prefix="gap03-exact-acl-") as tmpdir:
            graph = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, object_node_id TEXT, classification TEXT,
                        allowed_audiences_json TEXT, canonical_status TEXT, validation_state TEXT, currentness_state TEXT,
                        retrieval_eligible INTEGER, qualifiers_json TEXT);
                    CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT);
                    CREATE TABLE search_document (search_document_id TEXT, object_type TEXT, object_id TEXT, title TEXT, body TEXT,
                        canonical_status TEXT, currentness_state TEXT, retrieval_eligible INTEGER, source_id TEXT, assertion_id TEXT);
                    """
                )
                conn.execute("INSERT INTO node VALUES ('n-pdlc', 'PDLC', 'PDLC', 'workstream', 1)")
                conn.execute(
                    "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "ass-public-partner_advisory-only",
                        "n-pdlc",
                        None,
                        "public",
                        json.dumps(["internal_consulting"]),
                        "canonical",
                        "approved",
                        "current",
                        1,
                        json.dumps({}),
                    ),
                )
                conn.execute("INSERT INTO assertion_evidence VALUES ('ass-public-partner_advisory-only', 'ev-1', 'supports')")
                conn.execute(
                    "INSERT INTO search_document VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "sd-1",
                        "assertion",
                        "ass-public-partner_advisory-only",
                        "PDLC public partner_advisory note",
                        "PDLC note scoped to internal_consulting.",
                        "canonical",
                        "current",
                        1,
                        "src-1",
                        "ass-public-partner_advisory-only",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            public_res = exact_retrieval(
                "What is the status of PDLC?",
                graph,
                classification="public",
                audiences=["nee-engineering"],
                retrieval_mode="factual",
            )
            self.assertEqual([], public_res["exact_anchors"])

            internal_res = exact_retrieval(
                "What is the status of PDLC?",
                graph,
                classification="internal",
                audiences=["nee-engineering"],
                retrieval_mode="factual",
            )
            self.assertEqual(1, len(internal_res["exact_anchors"]))

    def test_gap04_ptc_hybrid_executor_overfetches_for_role_filter_and_enforces_connected_hops_e2e(self) -> None:
        """GAP-04 E2E: PTCHybridQueryExecutor retrieves role-matching facts beyond raw limit and enforces connected hop expansion."""
        raw_facts = [
            {"node_id": "f1", "name": "E1", "type": "entity", "snippet": "O1", "evidence_id": "ev1", "role": "context"},
            {"node_id": "f2", "name": "E2", "type": "entity", "snippet": "O2", "evidence_id": "ev2", "role": "context"},
            {
                "node_id": "f3",
                "name": "E3",
                "type": "entity",
                "snippet": "O3",
                "evidence_id": "ev3",
                "role": "risk",
                "connected_edges": [
                    {"from_node_id": "f3", "to_node_id": "N1", "relationship": "CAUSES", "hop": 1},
                    {"from_node_id": "N1", "to_node_id": "N2", "relationship": "IMPACTS", "hop": 2},
                    {"from_node_id": "DISCONNECTED_X", "to_node_id": "DISCONNECTED_Y", "relationship": "LEAKS", "hop": 2},
                ],
            },
            {"node_id": "f4", "name": "E4", "type": "entity", "snippet": "O4", "evidence_id": "ev4", "role": "risk"},
        ]
        executor = PTCHybridQueryExecutor(retrieval_engine=_StubRoleRetriever(raw_facts))
        plan = PTCHybridQueryPlan(
            query="key risks",
            limit=2,
            allowed_roles=["risk"],
            expand_subgraph=True,
            max_subgraph_depth=2,
        )
        summary = executor.execute(plan)
        self.assertEqual(2, summary.total_matches)
        self.assertEqual(("f3", "f4"), tuple(f["node_id"] for f in summary.top_facts))
        subgraph_node_ids = {n["node_id"] for n in summary.subgraph_nodes}
        self.assertIn("N1", subgraph_node_ids)
        self.assertIn("N2", subgraph_node_ids)
        self.assertNotIn("DISCONNECTED_Y", subgraph_node_ids)

    def test_gap05_evaluate_candidate_retrieval_evaluates_all_11_gates_dynamically_e2e(self) -> None:
        """GAP-05 E2E: evaluate_candidate_retrieval fails closed on empty candidates list and evaluates latency/critical gates dynamically."""
        empty_res = evaluate_candidate_retrieval("test query", candidates=[])
        self.assertEqual("REJECTED", empty_res["status"])
        self.assertFalse(empty_res["passed_11_gates"])

        failing_cand = [
            {
                "id": "c1",
                "score": 0.92,
                "latency_ms": 8500.0,
                "critical_failure": True,
            }
        ]
        fail_res = evaluate_candidate_retrieval("test query", candidates=failing_cand)
        self.assertEqual("REJECTED", fail_res["status"])
        self.assertFalse(fail_res["passed_11_gates"])
        self.assertEqual("FAIL", fail_res["gate_verdicts"]["latency_threshold"])
        self.assertEqual("FAIL", fail_res["gate_verdicts"]["critical_cases"])

        passing_cand = [
            {
                "id": "c1",
                "score": 0.92,
                "latency_ms": 120.0,
                "critical_failure": False,
            }
        ]
        pass_res = evaluate_candidate_retrieval("test query", candidates=passing_cand)
        self.assertEqual("QUALIFIED", pass_res["status"])
        self.assertTrue(pass_res["passed_11_gates"])

    def test_gap06_get_backend_accepts_canonical_active_pair_backend_ids_e2e(self) -> None:
        """GAP-06 E2E: get_backend resolves canonical flagembedding-bge-m3* backend IDs from active_pair.json."""
        dev_backend = get_backend("flagembedding-bge-m3-dev", allow_dev_backend=True)
        self.assertIsInstance(dev_backend, DevelopmentFlagEmbeddingBackend)
        self.assertEqual("flagembedding-bge-m3-dev", dev_backend.backend_id)


if __name__ == "__main__":
    unittest.main()
