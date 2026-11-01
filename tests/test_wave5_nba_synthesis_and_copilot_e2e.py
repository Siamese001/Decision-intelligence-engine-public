"""Wave 5 End-to-End Tests: Domain-Agnostic NBA Reasoning, Markdown Rendering & Copilot / Navigator Unification."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.query_core.anti_conflation import validate_answer_anti_conflation
from dual_pipeline.query_core.navigator_agent import DEFAULT_BRIDGE_PREDICATES, MultiHopQueryNavigatorAgent
from dual_pipeline.query_core.reasoning import (
    reason_over_packet,
    render_markdown,
    validate_answer,
)


class Wave5NBASynthesisAndCopilotE2ETests(unittest.TestCase):
    def test_energy_intelligence_nba_synthesis_and_markdown_rendering(self) -> None:
        """reason_over_packet synthesizes concrete Next Best Actions from Energy Intelligence Strategic Frames and renders them in answer.md."""
        plan = {
            "query_type": "opportunity_nba",
            "retrieval_mode": "strategic",
            "allowed_lanes": ["approved", "candidate", "external_public"],
            "primary_entities": ["Apex Global Enterprises", "Google Cloud"],
        }
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-energy-stance",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "NEE Strategic Stance on Hyperscale Data Center PPAs & BESS Augmentation",
                        "retrieval_text": (
                            "Strategic Frame: Apex Global Enterprises evaluates 8+ GW Google Cloud hyperscale data center "
                            "co-location and BESS fleet augmentation to mitigate tariff and grid interconnection risks."
                        ),
                        "evidence_ids": ["ev-energy-1"],
                    },
                    "retrieval_metadata": {"answer_use": "strategic_context"},
                    "graph_path": {"predicate": "DELIVERS"},
                    "evidence": [{"evidence_id": "ev-energy-1"}],
                },
                {
                    "record": {
                        "record_id": "rec-energy-risk",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "BESS Cell Degradation & FERC Interconnection Queue Constraint",
                        "retrieval_text": (
                            "BESS thermal degradation and FERC interconnection queue backlog constrain near-term "
                            "dispatch capacity and create SLA exposure."
                        ),
                        "evidence_ids": ["ev-energy-2"],
                    },
                    "retrieval_metadata": {"answer_use": "decision_constraint"},
                    "graph_path": {"predicate": "HAS_RISK"},
                    "evidence": [{"evidence_id": "ev-energy-2"}],
                },
                {
                    "record": {
                        "record_id": "rec-energy-action",
                        "record_type": "action_item",
                        "lane": "approved",
                        "title": "Deploy Vertex AI Field Operations & BESS Telemetry Twin",
                        "retrieval_text": (
                            "Co-develop Google Cloud Vertex AI predictive maintenance twin for BESS augmentation "
                            "and hyperscale PPA dispatch optimization."
                        ),
                        "evidence_ids": ["ev-energy-3"],
                    },
                    "retrieval_metadata": {"answer_use": "guidance"},
                    "evidence": [{"evidence_id": "ev-energy-3"}],
                },
            ],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "gaps": [],
        }

        answer = reason_over_packet(plan, packet)
        validation = validate_answer(answer, packet)
        self.assertEqual("PASS", validation["status"], f"Validation failed: {validation}")

        nbas = answer["next_best_actions"]
        self.assertGreaterEqual(len(nbas), 2)
        nba_texts = " ".join(item["action"] for item in nbas)
        self.assertIn("Vertex AI", nba_texts)
        self.assertIn("NEE Strategic Stance", nba_texts)
        self.assertNotIn(
            "Validate the highest-ranked opportunity-to-pain-point mapping with the accountable workstream owner.",
            nba_texts,
        )

        md = render_markdown(answer, "X3A_APPROVED")
        self.assertIn("## Next best actions", md)
        self.assertIn("Vertex AI", md)
        self.assertIn("`rec-energy-action`", md)

    def test_multi_workstream_comparison_passes_anti_conflation_when_corroborated(self) -> None:
        """Cross-workstream synthesis statements comparing two workstreams pass anti-conflation when both workstreams are cited in packet."""
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-sherpa",
                        "title": "Project Beacon OpenSearch architecture",
                        "retrieval_text": "Project Beacon uses OpenSearch for knowledge retrieval.",
                        "evidence_ids": ["ev-sherpa"],
                    }
                },
                {
                    "record": {
                        "record_id": "rec-dociq",
                        "title": "DocIQ document pipeline",
                        "retrieval_text": "DocIQ processes engineering PDFs and integrates retrieval patterns.",
                        "evidence_ids": ["ev-dociq"],
                    }
                },
            ],
            "candidate": [],
            "external_public": [],
        }
        answer = {
            "current_state": [
                {
                    "statement": "Both Project Beacon and DocIQ share retrieval patterns across engineering workflows.",
                    "citations": ["rec-sherpa", "rec-dociq"],
                    "evidence_ids": ["ev-sherpa", "ev-dociq"],
                    "lane": "approved",
                }
            ],
            "pain_points": [],
            "opportunities": [],
            "guidance": [],
            "hypotheses": [],
        }
        failures = validate_answer_anti_conflation(answer, packet)
        self.assertEqual([], failures)

    def test_navigator_agent_and_copilot_multi_hop_chain_actions(self) -> None:
        """MultiHopQueryNavigatorAgent expands canonical names across expanded bridge predicates, and CopilotGraphRetriever surfaces connected chain actions."""
        self.assertIn("OWNS_OR_LEADS", DEFAULT_BRIDGE_PREDICATES)
        self.assertIn("DELIVERS", DEFAULT_BRIDGE_PREDICATES)
        self.assertIn("GOVERNS", DEFAULT_BRIDGE_PREDICATES)

        nav = MultiHopQueryNavigatorAgent(max_steps=2, max_tool_calls=6)
        assertions = [
            {
                "assertion_id": "a1",
                "subject_id": "entity-nee-1",
                "subject_name": "Apex Global Enterprises",
                "predicate": "DELIVERS",
                "object_id": "entity-bess-1",
                "object_name": "BESS Augmentation Program",
                "claim_text": "Apex Global Enterprises delivers BESS Augmentation Program for grid reliability.",
            },
            {
                "assertion_id": "a2",
                "subject_id": "entity-bess-1",
                "subject_name": "BESS Augmentation Program",
                "predicate": "OWNS_OR_LEADS",
                "object_id": "person-lead-1",
                "object_name": "Energy Storage Lead",
                "claim_text": "BESS Augmentation Program is led by Energy Storage Lead.",
            },
        ]
        res = nav.navigate(
            query="Who leads the BESS Augmentation Program delivered by Apex Global Enterprises?",
            seed_entities=["Apex Global Enterprises"],
            subquestions=["Who leads the BESS Augmentation Program?"],
            assertions=assertions,
        )
        self.assertEqual(2, len(res.expanded_assertions))

        topology = SimpleNamespace(
            nodes={
                "node_sherpa": {"node_id": "node_sherpa", "canonical_name": "Project Beacon", "node_type": "workstream"},
                "node_os": {"node_id": "node_os", "canonical_name": "OpenSearch Migration", "node_type": "deliverable"},
                "person:ninan": {"node_id": "person:ninan", "canonical_name": "Speaker_Beta Tharakan", "node_type": "person"},
            },
            assertions={
                "asrt_1": {
                    "assertion_id": "asrt_1",
                    "subject_node_id": "node_sherpa",
                    "predicate": "CONTAINS",
                    "object_node_id": "node_os",
                    "claim_text": "Project Beacon contains OpenSearch Migration workstream.",
                    "canonical_status": "canonical",
                    "canonical_eligible": 1,
                    "retrieval_eligible": 1,
                    "confidence_score": 0.95,
                    "is_enterprise_policy": True,
                },
                "asrt_2": {
                    "assertion_id": "asrt_2",
                    "subject_node_id": "person:ninan",
                    "predicate": "DELIVERS",
                    "object_node_id": "node_os",
                    "claim_text": "Speaker_Beta Tharakan delivers the OpenSearch Migration cutover.",
                    "canonical_status": "canonical",
                    "canonical_eligible": 1,
                    "retrieval_eligible": 1,
                    "confidence_score": 0.95,
                    "is_enterprise_policy": True,
                },
            },
            forward_adjacency={
                "node_sherpa": [{"to_node_id": "node_os", "predicate": "CONTAINS", "claim_text": "Project Beacon contains OpenSearch Migration"}],
                "node_os": [{"to_node_id": "person:ninan", "predicate": "DELIVERS", "claim_text": "Speaker_Beta Tharakan delivers OpenSearch Migration"}],
            },
        )
        retriever = CopilotGraphRetriever(topology=topology)
        actions = retriever.get_next_actions("Project Beacon")
        self.assertTrue(any(a["assertion_id"] == "asrt_2" for a in actions))


if __name__ == "__main__":
    unittest.main()
