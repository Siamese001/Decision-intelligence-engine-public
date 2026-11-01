from __future__ import annotations

import unittest

from dual_pipeline.decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
    build_viable_options,
    evaluate_decision,
)


def _packet() -> dict[str, object]:
    def row(record_id: str, title: str, text: str, evidence_id: str) -> dict[str, object]:
        return {
            "record": {
                "record_id": record_id,
                "title": title,
                "retrieval_text": text,
                "record_type": "assertion",
                "evidence_ids": [evidence_id],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "factual_answer"},
        }

    return {
        "approved": [
            row("risk-record", "Risk", "Delay risk affects delivery", "evidence-risk"),
            row("alpha-record", "Alpha", "Alpha improves impact and cost", "evidence-alpha"),
            row("beta-record", "Beta", "Beta improves impact", "evidence-beta"),
        ],
        "candidate": [],
        "external_public": [],
        "graph_paths": [{"path_id": "path-1", "nodes": ["alpha", "impact"]}],
        "counterevidence": [],
        "gaps": [],
    }


def _evidence() -> dict[str, object]:
    return build_decision_evidence_pack(
        query_result={
            "generation_id": "generation-1",
            "graph_snapshot": "snapshot-1",
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "test",
            "bm25_executed": True,
            "dense_executed": True,
            "sparse_executed": True,
            "colbert_executed": True,
            "graph_traversal_executed": True,
        },
        evidence_packet=_packet(),
        retrieval_report={
            "terminal_coverage_disposition": "COMPLETE",
            "coverage_counts": {"supported": 2},
        },
    )


def _candidate_evidence() -> dict[str, object]:
    packet = _packet()
    packet["candidate"] = [
        {
            "record": {
                "record_id": "candidate-record",
                "title": "Nightly evaluation candidate",
                "retrieval_text": "Sherpa evaluation is running nightly.",
                "record_type": "assertion",
                "evidence_ids": ["candidate-evidence"],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "candidate_hypothesis"},
        }
    ]
    return build_decision_evidence_pack(
        query_result={
            "generation_id": "generation-1",
            "graph_snapshot": "snapshot-1",
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "test",
        },
        evidence_packet=packet,
    )


class DecisionEngineTests(unittest.TestCase):
    def test_missing_criteria_requires_clarification(self) -> None:
        frame = build_decision_frame(question="Which option should NEE choose?")
        self.assertEqual("CLARIFICATION_REQUIRED", frame["frame_state"])
        self.assertEqual(["criteria"], frame["missing_fields"])

    def test_explicit_question_clause_builds_criteria(self) -> None:
        frame = build_decision_frame(
            question="Which option should NEE choose given impact, cost, and risk?"
        )
        self.assertEqual("READY", frame["frame_state"])
        self.assertEqual(
            ["impact", "cost", "risk"], [row["label"] for row in frame["criteria"]]
        )

    def test_top_retrieval_risk_never_becomes_an_option(self) -> None:
        frame = build_decision_frame(
            question="Choose between Alpha and Beta",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        options = build_viable_options(frame=frame, evidence_pack=_evidence(), answer=None)
        self.assertEqual(["Alpha", "Beta"], [row["label"] for row in options])
        self.assertNotIn("Risk", [row["label"] for row in options])

    def test_criteria_not_retrieval_rank_selects_unique_option(self) -> None:
        frame = build_decision_frame(
            question="Choose between Alpha and Beta",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        result = evaluate_decision(
            frame=frame,
            evidence_pack=_evidence(),
            answer=None,
            execution={"query_run_id": "run-1"},
        )
        self.assertEqual("DECISION_PRODUCED", result["terminal_state"])
        self.assertEqual("Alpha", result["recommended_option"]["label"])
        self.assertEqual(["evidence-alpha"], result["material_conclusions"][0]["evidence_ids"])

    def test_incomplete_reasoning_returns_evidence_ready_not_decision(self) -> None:
        frame = build_decision_frame(
            question="Choose between Alpha and Beta",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        result = evaluate_decision(
            frame=frame,
            evidence_pack=_evidence(),
            answer=None,
            execution={"query_run_id": "run-1"},
            allow_recommendation=False,
        )
        self.assertEqual(
            "EVIDENCE_READY_DECISION_NOT_PRODUCED", result["terminal_state"]
        )
        self.assertIsNone(result["recommended_option"])

    def test_candidate_evidence_never_promotes_the_candidate_fact(self) -> None:
        frame = build_decision_frame(
            question="Should the candidate be promoted or kept?",
            options=["Promote the candidate", "Keep the candidate"],
            criteria=["source-bound evidence"],
        )
        result = evaluate_decision(
            frame=frame,
            evidence_pack=_candidate_evidence(),
            answer=None,
            execution={"query_run_id": "run-candidate"},
            query_type="candidate_promotion_review",
        )
        self.assertEqual("DECISION_PRODUCED", result["terminal_state"])
        self.assertEqual("Keep the candidate", result["recommended_option"]["label"])
        self.assertEqual([], result["recommended_option"]["evidence_ids"])
        self.assertEqual(
            ["candidate-evidence"], result["material_conclusions"][0]["evidence_ids"]
        )

    def test_witness_subgraph_integration_in_decision_result(self) -> None:
        from pathlib import Path

        from dual_pipeline.in_memory_graph import InMemoryGraphTopology

        frame = build_decision_frame(
            question="Choose between Alpha and Beta",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        topology = InMemoryGraphTopology(
            graph_path=Path("dummy.sqlite"),
            graph_sha256="1" * 64,
            nodes={"alpha": {"node_id": "alpha", "canonical_name": "Alpha", "node_type": "option"}},
            assertions={},
            supporting_evidence={"assert-1": ["evidence-alpha"]},
            contradicting_evidence={},
            forward_adjacency={},
            reverse_adjacency={},
            undirected_adjacency={},
            canonical_edges=[],
        )
        active_pair = {
            "generation_id": "gen-test-1",
            "graph_sha256": "1" * 64,
            "sidecar_sha256": "2" * 64,
        }
        result = evaluate_decision(
            frame=frame,
            evidence_pack=_evidence(),
            answer=None,
            execution={"query_run_id": "run-1"},
            topology=topology,
            active_pair=active_pair,
        )
        self.assertEqual("DECISION_PRODUCED", result["terminal_state"])
        self.assertIn("com.partner_advisory.nee.decision-witness-subgraph", result["extensions"])
        witness = result["extensions"]["com.partner_advisory.nee.decision-witness-subgraph"]
        self.assertEqual(
            "urn:nee:dual-pipeline:decision-witness-subgraph:1.0.0",
            witness["contract_id"],
        )
        self.assertEqual("1" * 64, witness["graph_sha256"])


if __name__ == "__main__":
    unittest.main()
