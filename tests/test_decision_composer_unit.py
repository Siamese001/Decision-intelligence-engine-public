#!/usr/bin/env python3
"""Unit tests for dual_pipeline.decision_composer."""
from __future__ import annotations

import unittest

from dual_pipeline.decision_composer import compose_decision_markdown
from dual_pipeline.decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
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


class TestDecisionComposerUnit(unittest.TestCase):
    """Tests for compose_decision_markdown rendering."""

    def test_compose_decision_produced(self) -> None:
        frame = build_decision_frame(
            question="Choose between Alpha and Beta given impact and cost",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        result = evaluate_decision(
            frame=frame,
            evidence_pack=_evidence(),
            answer=None,
            execution={"query_run_id": "run-1"},
        )
        md = compose_decision_markdown(result)
        self.assertIn("# Decision result: DECISION_PRODUCED", md)
        self.assertIn("**Question:**", md)
        self.assertIn("## Recommendation", md)
        self.assertIn("Alpha", md)
        self.assertIn("## Options and trade-offs", md)
        self.assertIn("## Risks and unknowns", md)
        self.assertIn("## Evidence", md)

    def test_compose_no_decision_or_clarification(self) -> None:
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
        md = compose_decision_markdown(result)
        self.assertIn("## Recommendation", md)
        self.assertIn("No supported recommendation was produced.", md)


if __name__ == "__main__":
    unittest.main()
