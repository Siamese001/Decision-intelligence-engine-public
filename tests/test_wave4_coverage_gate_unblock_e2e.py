"""Wave 4 End-to-End Tests: Pre-L2 Coverage Manifest, Directional Support & Decision Support Gate Remediation."""
from __future__ import annotations

import unittest

from dual_pipeline.l0.decision_support_contracts import (
    DecisionSupportState,
    compile_frame_evidence_requirements,
    evaluate_evidence_support,
)
from dual_pipeline.l2 import query_retrieval_policy
from dual_pipeline.query_core.coverage import (
    build_retrieval_evidence_report,
    compile_query_coverage_manifest,
    coverage_complete,
)
from dual_pipeline.query_core.planning import _resolve_evidence_requirements

ACTIVE = {
    "graph_snapshot": "graph-snapshot-wave4-e2e",
    "graph_sha256": "a" * 64,
}


class Wave4CoverageGateUnblockE2ETests(unittest.TestCase):
    def test_all_nine_query_types_and_six_retrieval_modes_pass_coverage_report(self) -> None:
        """All 9 canonical query_type routes and all 6 retrieval_mode policies pass build_retrieval_evidence_report."""
        query_types = (
            "exact_lookup",
            "current_state_corroboration",
            "project_status_current",
            "project_status_history",
            "relationship_explanation",
            "cross_workstream_synthesis",
            "gap_risk_inconsistency",
            "opportunity_nba",
            "candidate_promotion_review",
        )
        for qtype in query_types:
            retrieval_mode, lanes = query_retrieval_policy(qtype)
            ev_reqs = _resolve_evidence_requirements(qtype, retrieval_mode)
            plan = {
                "run_id": f"run-w4-{qtype}",
                "query_type": qtype,
                "allowed_lanes": list(lanes),
                "materiality": "high",
                "primary_entities": ["Apex Global Enterprises"],
                "evidence_requirements": ev_reqs,
                "subquestions": ["What is the Apex Global Enterprises posture?"],
                "canonical_digest": {"value": "b" * 64},
            }
            manifest = compile_query_coverage_manifest(
                question="Recommend Apex Global Enterprises posture and options.",
                validated_request_digest="c" * 64,
                plan=plan,
                active=ACTIVE,
                authorization_scope_digest="d" * 64,
            )
            comprehensive_text = (
                "Apex Global Enterprises current governed state effective dates latest dated project-status "
                "snapshot as-of date citation-only source evidence relationship paths direction "
                "supporting contradicting recurring patterns material differences constraints "
                "unresolved gaps observed risks conflicts counterevidence resolution business needs "
                "opportunities expected outcomes dependencies prioritization rationale candidate "
                "assertions proposal-only posture approved corroboration validation"
            )
            packet = {
                "graph_snapshot": ACTIVE["graph_snapshot"],
                "packet_digest": "e" * 64,
                "allowed_lanes": list(lanes),
                "approved": [
                    {
                        "record": {
                            "record_id": f"rec-app-{qtype}",
                            "title": "Apex Global Enterprises governed record",
                            "retrieval_text": comprehensive_text,
                            "evidence_ids": ["ev-1"],
                        },
                        "graph_path": {
                            "subject": "Apex Global Enterprises",
                            "predicate": "HAS_CLAIM_TEXT",
                            "object": comprehensive_text,
                            "steps": [{"traversal_direction": "reverse", "edge_type": "CONTRIBUTES_TO"}],
                        },
                        "evidence": [{"evidence_id": "ev-1"}],
                    }
                ],
                "candidate": [
                    {
                        "record": {
                            "record_id": f"rec-cand-{qtype}",
                            "title": "Apex Global Enterprises candidate proposal",
                            "retrieval_text": comprehensive_text,
                            "evidence_ids": ["ev-2"],
                        },
                        "evidence": [{"evidence_id": "ev-2"}],
                    }
                ]
                if "candidate" in lanes
                else [],
                "external_public": [],
                "counterevidence": [
                    {
                        "counterevidence_id": "ce-1",
                        "record_id": f"rec-app-{qtype}",
                        "assertion_id": "ass-1",
                    }
                ],
            }
            report = build_retrieval_evidence_report(
                coverage_manifest=manifest,
                packet=packet,
                authorization_scope_digest="d" * 64,
                graph_sha256=ACTIVE["graph_sha256"],
                view_authorized=True,
            )
            self.assertTrue(
                coverage_complete(report),
                f"Expected coverage_complete=True for {qtype} ({retrieval_mode}), got {report['requirement_outcomes']}",
            )
            self.assertTrue(report["l2_input_record_ids_by_lane"]["approved"])

    def test_decision_frame_obligations_do_not_shadow_into_policy_requirements_and_support_retrieval_text(self) -> None:
        """DecisionFrame obligations evaluate retrieval_text and lowercase option nouns without shadowing as policy_requirement."""
        frame = {
            "frame_digest": "f" * 64,
            "options": [{"option_id": "opt-bess", "label": "augmentation of BESS fleet"}],
            "criteria": [{"criterion_id": "crit-rel", "label": "grid reliability and peak capacity"}],
            "constraints": ["must comply with FERC tariff rules"],
            "time_horizon": "current",
        }
        frame_reqs = compile_frame_evidence_requirements(frame, default_materiality="high")
        plan = {
            "run_id": "run-w4-frame",
            "query_type": "opportunity_nba",
            "allowed_lanes": ["approved", "candidate", "external_public"],
            "materiality": "high",
            "primary_entities": ["Apex Global Enterprises"],
            "evidence_requirements": _resolve_evidence_requirements("opportunity_nba", "strategic"),
            "subquestions": ["Which BESS option satisfies grid reliability?"],
            "extensions": {
                "com.partner_advisory.nee.decision-frame": {
                    "frame_digest": frame["frame_digest"],
                    "requirements": frame_reqs,
                }
            },
            "canonical_digest": {"value": "b" * 64},
        }
        manifest = compile_query_coverage_manifest(
            question="Evaluate augmentation of BESS fleet for Apex Global Enterprises.",
            validated_request_digest="c" * 64,
            plan=plan,
            active=ACTIVE,
            authorization_scope_digest="d" * 64,
        )
        kinds_by_label = {r["requirement_label"]: r["requirement_kind"] for r in manifest["requirements"]}
        self.assertEqual(
            "policy_requirement",
            kinds_by_label["[augmentation of BESS fleet] satisfies grid reliability and peak capacity"],
        )
        self.assertEqual(
            "policy_requirement",
            kinds_by_label["Constraint satisfaction: must comply with FERC tariff rules"],
        )
        self.assertEqual(
            "policy_requirement",
            kinds_by_label["Temporal validity: current"],
        )

        # Verify evaluate_evidence_support reads retrieval_text and does not false-trigger on 'no outage failure'
        sidecar_record = {
            "record_id": "rec-bess-1",
            "title": "Apex Global Enterprises storage posture",
            "retrieval_text": (
                "Apex Global Enterprises augmentation of BESS fleet improves grid reliability and peak capacity "
                "with no outage failure and complies with FERC tariff rules; business needs opportunities "
                "expected outcomes dependencies constraints risks prioritization rationale."
            ),
            "currentness_state": "current",
            "evidence_ids": ["ev-bess-1"],
        }
        state, reason = evaluate_evidence_support(
            sidecar_record,
            "augmentation of BESS fleet",
            "grid reliability and peak capacity",
        )
        self.assertEqual(DecisionSupportState.SUPPORTED, state, reason)

        packet = {
            "graph_snapshot": ACTIVE["graph_snapshot"],
            "packet_digest": "e" * 64,
            "allowed_lanes": ["approved", "candidate", "external_public"],
            "approved": [{"record": sidecar_record, "evidence": [{"evidence_id": "ev-bess-1"}]}],
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
        self.assertTrue(coverage_complete(report))
        self.assertEqual(["rec-bess-1"], report["l2_input_record_ids_by_lane"]["approved"])


if __name__ == "__main__":
    unittest.main()
