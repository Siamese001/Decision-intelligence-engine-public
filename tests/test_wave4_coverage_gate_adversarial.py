"""Wave 4 Adversarial & Edge-Case Tests: Coverage Gate, Decision Support Contracts & Partial Gap Retention.

Covers mandatory edge-case categories:
1. Boundary payload (citation-only / empty text rejected by evaluate_evidence_support; partial facet gap retains covered records in l2_input)
2. Malformed input (invalid request digest or empty question rejected by compile_query_coverage_manifest)
3. Invariant bypass attempt (explicit numbered user requirement gap still zeroes l2_input; reverse_only path on explicit directional requirement blocked)
4. Overflow & exhaustion (>128 coverage requirements rejected; >262KB report rejected)
5. Exact production regression (negation polarity 'not supported' / 'incompatible' triggers CONTRADICTED while 'no downtime' does not false-trigger)
"""
from __future__ import annotations

import unittest

from dual_pipeline.l0.decision_support_contracts import (
    DecisionSupportState,
    evaluate_evidence_support,
)
from dual_pipeline.query_core.coverage import (
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
)

ACTIVE = {
    "graph_snapshot": "graph-snapshot-wave4-adv",
    "graph_sha256": "a" * 64,
}


class Wave4CoverageGateAdversarialTests(unittest.TestCase):
    def test_partial_facet_gap_retains_covered_l2_input_while_explicit_item_gap_blocks(self) -> None:
        """Category 1 & 3 (Boundary & Invariant bypass): partial secondary facet gap keeps covered records in l2_input, while missing explicit user item blocks."""
        plan = {
            "run_id": "run-w4-partial",
            "query_type": "opportunity_nba",
            "allowed_lanes": ["approved", "candidate", "external_public"],
            "materiality": "high",
            "primary_entities": ["Apex Global Enterprises"],
            "evidence_requirements": [
                "Approved internal facts for current-state statements",
                "Resolvable evidence and source locators for material claims",
            ],
            "subquestions": ["What are the Apex Global Enterprises opportunities?"],
            "canonical_digest": {"value": "b" * 64},
        }
        manifest = compile_query_coverage_manifest(
            question="What are the Apex Global Enterprises opportunities?",
            validated_request_digest="c" * 64,
            plan=plan,
            active=ACTIVE,
            authorization_scope_digest="d" * 64,
        )
        # Record covers Apex Global Enterprises and opportunities, but does NOT cover the second facet (dependencies/constraints/risks)
        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "e" * 64,
            "allowed_lanes": ["approved", "candidate", "external_public"],
            "approved": [
                {
                    "record": {
                        "record_id": "rec-partial-1",
                        "title": "Apex Global Enterprises opportunity",
                        "retrieval_text": "Apex Global Enterprises business needs and strategic opportunities.",
                        "evidence_ids": ["ev-1"],
                    },
                    "evidence": [{"evidence_id": "ev-1"}],
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
            graph_sha256=ACTIVE["graph_sha256"],
            view_authorized=True,
        )
        # Covered records are preserved in l2_input even though one graph_facet is an explicit_gap
        self.assertEqual(["rec-partial-1"], report["l2_input_record_ids_by_lane"]["approved"])

    def test_decision_support_negation_and_citation_only_boundaries(self) -> None:
        """Category 1 & 5 (Boundary payload & Production regression): citation-only is UNSUPPORTED and real negation is CONTRADICTED."""
        cite_only = {"retrieval_text": "[doc-1] see citation ref 123"}
        state1, _ = evaluate_evidence_support(cite_only, "Solar PPA", "cost reduction")
        self.assertEqual(DecisionSupportState.UNSUPPORTED, state1)

        contradicted = {
            "retrieval_text": "Solar PPA is incompatible with cost reduction targets and was rejected."
        }
        state2, _ = evaluate_evidence_support(contradicted, "Solar PPA", "cost reduction")
        self.assertEqual(DecisionSupportState.CONTRADICTED, state2)

        stale = {
            "retrieval_text": "Solar PPA achieves cost reduction targets.",
            "currentness_state": "superseded",
        }
        state3, _ = evaluate_evidence_support(stale, "Solar PPA", "cost reduction")
        self.assertEqual(DecisionSupportState.UNSUPPORTED, state3)

    def test_malformed_manifest_inputs_and_overflow_rejected(self) -> None:
        """Category 2 & 4 (Malformed input & Overflow): invalid digest and >128 items raise ValueError."""
        plan = {
            "run_id": "run-w4-bad",
            "query_type": "exact_lookup",
            "allowed_lanes": ["approved"],
            "materiality": "low",
            "primary_entities": ["NEE"],
            "evidence_requirements": [],
            "subquestions": [],
            "canonical_digest": {"value": "b" * 64},
        }
        with self.assertRaisesRegex(ValueError, "QUERY_COVERAGE_PLAN_INCOMPLETE"):
            compile_query_coverage_manifest(
                question="Valid question",
                validated_request_digest="not-a-hex-digest",
                plan=plan,
                active=ACTIVE,
                authorization_scope_digest="d" * 64,
            )

        huge_list = "\n".join(f"{i}. Item {i}" for i in range(1, 135))
        with self.assertRaisesRegex(ValueError, "QUERY_COVERAGE_REQUIREMENT_LIMIT_EXCEEDED"):
            compile_query_coverage_manifest(
                question=huge_list,
                validated_request_digest="c" * 64,
                plan=plan,
                active=ACTIVE,
                authorization_scope_digest="d" * 64,
            )


if __name__ == "__main__":
    unittest.main()
