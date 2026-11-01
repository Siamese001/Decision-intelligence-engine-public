#!/usr/bin/env python3
"""Wave 20 unit tests: Governed Dialectical Prompt Slot Composition."""
from __future__ import annotations

import unittest

from dual_pipeline.l2 import (
    _verify_governed_prompt,
    provider_quality_requirements,
)
from dual_pipeline.query import assemble_prompt, build_evidence_packet


class Wave20PromptCompositionTests(unittest.TestCase):
    def test_provider_quality_requirements_baseline(self) -> None:
        plan = {
            "retrieval_mode": "factual",
            "challenger_required": False,
            "assurance_profile": "standard",
        }
        evidence_packet = {
            "approved": [
                {"record": {"record_id": "rec_1"}},
                {"record": {"record_id": "rec_2"}},
            ],
            "candidate": [],
        }
        reqs = provider_quality_requirements(plan=plan, evidence_packet=evidence_packet)
        self.assertEqual(reqs["required_approved_citation_ids"], ["rec_1", "rec_2"])
        self.assertFalse(reqs["candidate_hypothesis_required"])
        self.assertNotIn("dialectical_reasoning_required", reqs)

    def test_provider_quality_requirements_dialectical_injection(self) -> None:
        # Activated via challenger_required
        plan_challenger = {
            "retrieval_mode": "candidate_review",
            "challenger_required": True,
            "assurance_profile": "standard",
        }
        evidence_packet = {
            "approved": [{"record": {"record_id": "rec_1"}}],
            "candidate": [
                {
                    "record": {"record_id": "cand_1"},
                    "retrieval_metadata": {"answer_use": "candidate_hypothesis"},
                }
            ],
        }
        reqs = provider_quality_requirements(
            plan=plan_challenger, evidence_packet=evidence_packet
        )
        self.assertTrue(reqs["candidate_hypothesis_required"])
        self.assertTrue(reqs["dialectical_reasoning_required"])
        self.assertIn("thesis_grounded_in_approved_facts", reqs["dialectical_structure"])

        # Activated via enhanced assurance profile
        plan_enhanced = {
            "retrieval_mode": "factual",
            "challenger_required": False,
            "assurance_profile": "enhanced",
        }
        reqs_enhanced = provider_quality_requirements(
            plan=plan_enhanced, evidence_packet={"approved": [], "candidate": []}
        )
        self.assertTrue(reqs_enhanced["dialectical_reasoning_required"])

    def test_assemble_and_verify_governed_prompt_consistency(self) -> None:
        hydrated = {
            "strata": {
                "approved": [
                    {
                        "record": {
                            "record_id": "app_1",
                            "title": "Approved Record 1",
                            "retrieval_text": "Text 1",
                            "evidence_ids": ["ev_1"],
                            "strategic_profile": None,
                        },
                        "retrieval_metadata": {"answer_use": "factual_context"},
                    }
                ],
                "candidate": [],
                "external_public": [],
            },
            "graph_paths": [],
        }
        exact = {
            "exact_anchors": [{"search_document_id": "doc_1"}],
            "bm25_anchors": [],
        }
        packet = build_evidence_packet(
            hydrated=hydrated,
            graph_snapshot="snapshot_abc",
            exact=exact,
            retrieval_mode="factual",
        )
        plan = {
            "query_type": "factual_lookup",
            "materiality": "high",
            "retrieval_mode": "factual",
            "subquestions": ["Subquestion 1"],
            "required_output_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "prompt_blueprint": "V5.5-standard",
            "challenger_required": True,
        }
        route = {"query_route": "factual_lookup"}
        prompt = assemble_prompt(
            question="What is the active status?",
            plan=plan,
            route=route,
            packet=packet,
        )

        from types import SimpleNamespace
        decision = SimpleNamespace(
            query_type="factual_lookup",
            route="factual_lookup",
            materiality="high",
        )

        # Invariant check: verification must succeed without raising
        _verify_governed_prompt(
            prompt=prompt,
            question="What is the active status?",
            plan=plan,
            decision=decision,
            evidence_packet=packet,
            evidence_packet_hash=packet["packet_digest"],
        )

        # Tampering check: tampering with R0 dialectical instructions must trigger ValueError
        tampered_prompt = dict(prompt)
        tampered_slots = dict(prompt["slots"])
        tampered_r0 = dict(tampered_slots["R0"])
        tampered_r0["dialectical_reasoning_required"] = False
        tampered_slots["R0"] = tampered_r0
        tampered_prompt["slots"] = tampered_slots

        with self.assertRaises(ValueError):
            _verify_governed_prompt(
                prompt=tampered_prompt,
                question="What is the active status?",
                plan=plan,
                decision=decision,
                evidence_packet=packet,
                evidence_packet_hash=packet["packet_digest"],
            )


if __name__ == "__main__":
    unittest.main()

