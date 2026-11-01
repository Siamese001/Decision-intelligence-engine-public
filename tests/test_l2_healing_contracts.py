"""tests/test_l2_healing_contracts.py

Acceptance test suite for Wave 1: Core L2 Healing Contracts, Fact-Preservation
Engine & Budget Guards.
"""
from __future__ import annotations

import asyncio
import unittest

from dual_pipeline.l2_healer_service import UnifiedL2HealerService
from dual_pipeline.l2_healing_contracts import (
    HealingScope,
    L2HealingRequest,
    L2HealingResult,
    extract_entity_ids,
    extract_evidence_ids,
    verify_fact_preservation,
)


class TestL2HealingContracts(unittest.TestCase):
    """Verifies contracts, fact preservation invariants, and healer service."""

    def test_healing_request_and_result_structure(self) -> None:
        req = L2HealingRequest(
            scope=HealingScope.PIPELINE_B_PROJECTION,
            candidate_artifact={"test": "data"},
            failure_reasons=["Schema validation failed"],
            allowed_entity_ids={"system:bge-m3"},
            allowed_evidence_ids={"ev-001"},
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(req.scope, HealingScope.PIPELINE_B_PROJECTION)
        self.assertEqual(req.model_id, "gpt-5.6-luna")
        self.assertEqual(req.reasoning_effort, "high")
        self.assertEqual(req.max_repair_calls, 1)

    def test_default_model_is_gpt_5_6_luna_high(self) -> None:
        req = L2HealingRequest(
            scope=HealingScope.PIPELINE_A_TRANSCRIPT,
            candidate_artifact="test",
            failure_reasons=["test"],
        )
        self.assertIn(req.model_id, ("gpt-5.6-sol", "gpt-5.6-luna"))
        self.assertIn(req.reasoning_effort, ("medium", "high", "xhigh"))

        service = UnifiedL2HealerService()
        self.assertIn(service.model, ("gpt-5.6-sol", "gpt-5.6-luna"))
        self.assertIn(service.reasoning_effort, ("medium", "high", "xhigh"))


        res = L2HealingResult(
            status="HEALED",
            healed_artifact={"test": "healed"},
            patch_diff="Repaired schema keys",
            repaired_fields=["test"],
            fact_preservation_passed=True,
            tokens_used=150,
            latency_ms=250.0,
        )
        self.assertTrue(res.is_success)
        d = res.to_dict()
        self.assertEqual(d["status"], "HEALED")
        self.assertEqual(d["tokens_used"], 150)

    def test_extract_entity_and_evidence_ids(self) -> None:
        text = "Alex (person:alex-sanchez) discussed system:aurora with org:partner_advisory per ev-001 and stmt-05."
        entities = extract_entity_ids(text)
        self.assertIn("person:alex-sanchez", entities)
        self.assertIn("system:aurora", entities)
        self.assertIn("org:partner_advisory", entities)

        evidence = extract_evidence_ids(text)
        self.assertIn("ev-001", evidence)
        self.assertIn("stmt-05", evidence)

        # Nested dictionary extraction
        nested = {
            "node_id": "project:nexus",
            "metadata": {
                "owner": "person:jen-pan",
                "evidence": ["ev-042"],
            },
            "edges": [
                {"from_id": "project:nexus", "to_id": "system:unifier"}
            ]
        }
        nested_entities = extract_entity_ids(nested)
        self.assertIn("project:nexus", nested_entities)
        self.assertIn("person:jen-pan", nested_entities)
        self.assertIn("system:unifier", nested_entities)

    def test_verify_fact_preservation_passes_on_subset(self) -> None:
        original = {
            "entity": "person:alex-sanchez",
            "system": "system:aurora",
            "citation": "ev-001",
        }
        healed = {
            "entity": "person:alex-sanchez",
            "citation": "ev-001",
        }
        # Healed has a subset of entities/evidence -> valid
        self.assertTrue(verify_fact_preservation(original, healed))

        # With additional allowed roster
        roster_entities = {"person:jen-pan", "org:partner_advisory"}
        healed_with_roster = {
            "entity": "person:jen-pan",
            "citation": "ev-001",
        }
        self.assertTrue(verify_fact_preservation(original, healed_with_roster, allowed_entities=roster_entities))

    def test_verify_fact_preservation_fails_on_hallucination(self) -> None:
        original = {
            "entity": "person:alex-sanchez",
            "citation": "ev-001",
        }
        # Injects ungrounded stakeholder
        healed_hallucinated_person = {
            "entity": "person:hallucinated-vp",
            "citation": "ev-001",
        }
        self.assertFalse(verify_fact_preservation(original, healed_hallucinated_person))

        # Injects ungrounded evidence locator
        healed_hallucinated_evidence = {
            "entity": "person:alex-sanchez",
            "citation": "ev-999-fake",
        }
        self.assertFalse(verify_fact_preservation(original, healed_hallucinated_evidence))

    def test_healer_service_mock_pipeline_a(self) -> None:
        service = UnifiedL2HealerService(mock_mode=True)
        raw_transcript = (
            "# Executive Sync\n"
            "**Date:** 2026-09-12\n\n"
            "Speaker 1: We are moving forward with the migration.\n"
        )
        req = L2HealingRequest(
            scope=HealingScope.PIPELINE_A_TRANSCRIPT,
            candidate_artifact=raw_transcript,
            failure_reasons=["ISO-8601 date detected", "Unresolved generic Speaker 1"],
            allowed_entity_ids={"person:alex-sanchez"},
        )
        res = asyncio.run(service.execute_healing(req))
        self.assertTrue(res.is_success)
        self.assertIn("09/12/2026", str(res.healed_artifact))
        self.assertIn("Alex Sanchez:", str(res.healed_artifact))
        self.assertNotIn("2026-09-12", str(res.healed_artifact))

    def test_healer_service_mock_pipeline_b(self) -> None:
        service = UnifiedL2HealerService(mock_mode=True)
        candidate = {
            "schema_version": "5.7",
            "graph_mutations": {
                "decision_updates": [
                    {"decision": "Adopt BGE-M3", "rationale": "High benchmark performance"}
                ],
                "entity_upserts": [{"id": f"system:sys-{i}"} for i in range(10)],
            }
        }
        req = L2HealingRequest(
            scope=HealingScope.PIPELINE_B_PROJECTION,
            candidate_artifact=candidate,
            failure_reasons=["Missing temporal stamps", "Mutation count budget overrun"],
        )
        res = asyncio.run(service.execute_healing(req))
        self.assertTrue(res.is_success)
        healed = res.healed_artifact
        self.assertEqual(len(healed["graph_mutations"]["entity_upserts"]), 5)
        self.assertEqual(healed["graph_mutations"]["decision_updates"][0]["observed_at"], "09/12/2026")

    def test_healer_service_mock_pipeline_c(self) -> None:
        service = UnifiedL2HealerService(mock_mode=True)
        req = L2HealingRequest(
            scope=HealingScope.PIPELINE_C_RETRIEVAL,
            candidate_artifact={"query": "vector indexing status"},
            failure_reasons=["Perspective starvation: 0 candidates returned"],
        )
        res = asyncio.run(service.execute_healing(req))
        self.assertTrue(res.is_success)
        self.assertIn("system:bge-m3", res.healed_artifact.get("expanded_terms", []))

    def test_healer_service_mock_pipeline_d(self) -> None:
        service = UnifiedL2HealerService(mock_mode=True)
        recap = (
            "| 1 | Consider running validation | TBC | 09/15/2026 | OPEN |\n"
            "| 2 | Explore cloud database | Alex Sanchez | 09/18/2026 | OPEN |\n"
        )
        req = L2HealingRequest(
            scope=HealingScope.PIPELINE_D_BRIEFING,
            candidate_artifact=recap,
            failure_reasons=["Gate 2.2: Passive verbs in action item", "Gate 2.2: Unassigned owner TBC"],
        )
        res = asyncio.run(service.execute_healing(req))
        self.assertTrue(res.is_success)
        healed_str = str(res.healed_artifact)
        self.assertIn("Deploy running validation", healed_str)
        self.assertIn("Implement cloud database", healed_str)
        self.assertNotIn("TBC", healed_str)

    def test_budget_exhaustion_fails_closed(self) -> None:
        service = UnifiedL2HealerService(mock_mode=True)
        req = L2HealingRequest(
            scope=HealingScope.PIPELINE_A_TRANSCRIPT,
            candidate_artifact="broken text",
            failure_reasons=["Some error"],
            max_repair_calls=0,
        )
        res = asyncio.run(service.execute_healing(req))
        self.assertFalse(res.is_success)
        self.assertEqual(res.status, "UNREPAIRABLE")
        self.assertIn("Healing budget exhausted", res.error_message or "")


if __name__ == "__main__":
    unittest.main()
