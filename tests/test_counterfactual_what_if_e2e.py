"""Wave 5 end-to-end tests for strategic constraint role inference and counterfactual what-if synthesis."""

from __future__ import annotations

import unittest

from dual_pipeline.core import digest
from dual_pipeline.decision_engine_core.assessment import assess_options
from dual_pipeline.decision_engine_core.evidence_pack import build_decision_evidence_pack
from dual_pipeline.decision_engine_core.frame import build_decision_frame
from dual_pipeline.decision_engine_core.options import build_viable_options
from dual_pipeline.query_core.reasoning import reason_over_packet, validate_answer
from dual_pipeline.strategic_reasoning_core.channels import build_strategic_channels
from dual_pipeline.strategic_reasoning_core.profiling import (
    answer_use_for_profile,
    strategic_profile_for_record,
)


class CounterfactualWhatIfE2ETests(unittest.TestCase):
    def test_four_constraint_domains_infer_decision_constraint_role(self) -> None:
        claims = [
            "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
            "Calibrate candidate screening against NEA 5%-10% qualification bar and planted anti-patterns code review.",
            "NECAP vs NESOP spend: PartnerAdvisory bid onshore at 45% higher rate than offshore SoftServe.",
            "Fieldglass badging requires 3 days per week onsite and IT begins deprovisioning two weeks prior to contract end.",
        ]
        for text in claims:
            with self.subTest(text=text):
                profile = strategic_profile_for_record(
                    record_type="canonical_assertion",
                    lane="approved",
                    classification="internal",
                    text=text,
                )
                self.assertEqual(profile["knowledge_role"], "constraint")
                use = answer_use_for_profile(
                    base_answer_use="factual_answer",
                    profile=profile,
                    lane="approved",
                )
                self.assertEqual(use, "decision_constraint")

    def test_end_to_end_counterfactual_what_if_binds_constraints_and_passes_x1(self) -> None:
        question = (
            "Provide honest analysis of Brad and Beau discussion on pods local to "
            "West Palm Beach area from the perspective of NEE - what might work "
            "and what will not work and explain why"
        )
        frame = build_decision_frame(question=question)
        self.assertEqual("READY", frame["frame_state"])
        self.assertGreaterEqual(len(frame["criteria"]), 4)
        self.assertEqual(len(frame["options"]), 2)

        seed_item = {
            "record": {
                "record_id": "assertion:asrt-brad-pod",
                "title": "Accelerate Power West Palm Beach pod proposal",
                "retrieval_text": "Accelerate Power proposes forward deployed teams billing 42 hours a week in West Palm Beach.",
                "record_type": "canonical_assertion",
                "lane": "approved",
                "evidence_ids": ["ev-brad-1"],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "factual_answer"},
        }
        constraint_specs = [
            (
                "assertion:asrt-chakra-pod-ratio",
                "NEA 2-to-3 engineer workstream ratio",
                "NEA standardizes on 2 to 3 full-stack engineers per workstream, phasing out segregated pods.",
                "ev-chakra-1",
            ),
            (
                "assertion:asrt-amit-screening-bar",
                "NEA 5%-10% technical qualification bar",
                "Calibrate candidate screening against NEA 5%-10% qualification bar and planted anti-patterns code review.",
                "ev-amit-1",
            ),
            (
                "assertion:asrt-hanson-sow-rate",
                "NECAP vs NESOP onshore rate premium",
                "NECAP vs NESOP spend: PartnerAdvisory bid onshore at 45% higher rate than offshore SoftServe.",
                "ev-hanson-1",
            ),
            (
                "assertion:asrt-rianne-badging-access",
                "Fieldglass onsite badging and IT deprovisioning",
                "Fieldglass badging requires 3 days per week onsite and IT begins deprovisioning two weeks prior to contract end.",
                "ev-rianne-1",
            ),
        ]
        approved_items = [seed_item]
        graph_paths = []
        for rec_id, title, text, ev_id in constraint_specs:
            prof = strategic_profile_for_record(
                record_type="canonical_assertion",
                lane="approved",
                classification="internal",
                text=text,
            )
            use = answer_use_for_profile(
                base_answer_use="factual_answer",
                profile=prof,
                lane="approved",
            )
            approved_items.append(
                {
                    "record": {
                        "record_id": rec_id,
                        "title": title,
                        "retrieval_text": text,
                        "record_type": "canonical_assertion",
                        "lane": "approved",
                        "evidence_ids": [ev_id],
                        "currentness_state": "current",
                        "strategic_profile": prof,
                        "answer_use": use,
                    },
                    "retrieval_metadata": {"answer_use": use},
                }
            )
            graph_paths.append(
                {
                    "record_id": rec_id,
                    "seed_record_id": "assertion:asrt-brad-pod",
                    "hop_count": 1,
                    "steps": [
                        {
                            "from_node_id": "entity_accelerate_power",
                            "to_node_id": rec_id,
                            "relationship_family": "SHARES_CAPABILITY_DOMAIN",
                        }
                    ],
                }
            )

        packet = {
            "graph_snapshot": "snap-wave5",
            "allowed_lanes": ["approved"],
            "approved": approved_items,
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "gaps": [],
            "exclusions": [],
            "graph_paths": graph_paths,
        }
        packet["packet_digest"] = digest(
            {k: v for k, v in packet.items() if k != "packet_digest"}
        )

        channels = build_strategic_channels(packet)
        self.assertEqual(channels["role_counts"].get("constraint"), 4)

        query_result = {
            "run_id": "run-wave5-e2e",
            "generation_id": "gen-wave5",
            "graph_snapshot": "snap-wave5",
            "graph_sha256_after": "f" * 64,
            "embedding_backend": "bge-m3",
            "bm25_executed": True,
            "dense_executed": True,
            "sparse_executed": True,
            "colbert_executed": True,
            "graph_traversal_executed": True,
        }
        evidence_pack = build_decision_evidence_pack(
            query_result=query_result,
            evidence_packet=packet,
            retrieval_report={
                "terminal_coverage_disposition": "covered",
                "coverage_counts": {"covered": 5},
            },
        )
        constraint_ids = {rec["record_id"] for rec in evidence_pack["constraints"]}
        for rec_id, _, _, _ in constraint_specs:
            self.assertIn(rec_id, constraint_ids)

        plan = {
            "query_type": "general_query",
            "retrieval_mode": "approved_only",
            "allowed_lanes": ["approved"],
            "primary_entities": ["Accelerate Power"],
        }
        answer = reason_over_packet(plan, packet)
        x1 = validate_answer(answer, packet)
        self.assertEqual(x1["status"], "PASS", msg=f"X1 failed: {x1}")
        pain_citations = {
            cit for row in answer["pain_points"] for cit in row["citations"]
        }
        for rec_id, _, _, _ in constraint_specs:
            self.assertIn(rec_id, pain_citations)

        viable_options = build_viable_options(
            frame=frame,
            evidence_pack=evidence_pack,
            answer=answer,
            query_type="general_query",
        )
        assessments = assess_options(
            frame=frame,
            evidence_pack=evidence_pack,
            options=viable_options,
            answer=answer,
            query_type="general_query",
        )
        all_violations = {
            vid for row in assessments for vid in row["constraint_violations"]
        }
        for rec_id, _, _, _ in constraint_specs:
            self.assertIn(rec_id, all_violations)


if __name__ == "__main__":
    unittest.main()
