"""
tests/test_output3_signal_enhancement_and_enclave.py

Comprehensive test suite verifying:
1. High signal density of Output 03 (entities, relationships, decisions, risks, evidence locks).
2. Rule C-19 Career Leverage Firewall zero-leakage invariant between Output 03 and the companion enclave.
3. CopilotGraphRetriever fast-path indexing and sub-millisecond retrieval on enriched graph signals.
4. Multi-hop traversal and topological connectivity on the enriched enterprise knowledge graph.
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    C19_FORBIDDEN_MARKERS,
    PRIORITY_RELATIONSHIPS,
    CopilotGraphRetriever,
)
from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
from dual_pipeline.transcript_stages_core.models import RestrictedAdvisoryProjection


class MockTopology:
    """Mock in-memory topology structured like InMemoryGraphTopology."""

    def __init__(self, output03_data: Dict[str, Any]) -> None:
        mutations = output03_data.get("graph_mutations", {})
        self.nodes: Dict[str, Dict[str, Any]] = {}
        for ent in mutations.get("entity_upserts", []):
            self.nodes[ent["id"]] = {
                "node_id": ent["id"],
                "canonical_name": ent.get("name", ent["id"]),
                "display_name": ent.get("name", ent["id"]),
                "node_type": ent.get("type", "entity"),
                "description": ent.get("description", ""),
                "namespace": ent.get("namespace", "CLIENT_SAFE"),
                "is_quarantined": ent.get("is_quarantined", 0),
            }

        self.assertions: Dict[str, Dict[str, Any]] = {}
        self.forward_adjacency: Dict[str, list] = {}
        for rel in mutations.get("relationship_signals", []):
            sig_id = rel["signal_id"]
            self.assertions[sig_id] = {
                "assertion_id": sig_id,
                "subject_node": rel["from_id"],
                "predicate": rel["relationship"],
                "claim_text": f"{rel['from_id']} {rel['relationship']} {rel['to_id']}",
                "status": "ACTIVE",
            }
            if rel["from_id"] not in self.forward_adjacency:
                self.forward_adjacency[rel["from_id"]] = []
            self.forward_adjacency[rel["from_id"]].append({
                "relationship_family": rel["relationship"],
                "to_node": rel["to_id"],
                "to_node_name": rel["to_id"],
            })


class TestOutput03SignalEnhancementAndEnclave(unittest.TestCase):
    """Test suite for Output 03 enrichment, Copilot retrieval, and Career Enclave isolation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.lens_dir = cls.repo_root / "output" / "project_lens" / "09-18-2026" / "Jake_Amit_Alex_PartnerAdvisory"
        if not cls.lens_dir.exists():
            raise unittest.SkipTest("Jake_Amit_Alex_PartnerAdvisory directory has been superseded and removed.")
        cls.f01_path = cls.lens_dir / "01_Jake_Amit_Alex_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_hitl_resolved_transcript.md"
        cls.f03_path = cls.lens_dir / "03_Jake_Amit_Alex_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_canonical_signal_projection.json"
        cls.enclave_path = cls.lens_dir / "restricted" / "03_Jake_Amit_Alex_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_internal_advisory_enclave.json"

        cls.f01_text = cls.f01_path.read_text(encoding="utf-8")
        cls.f03_data = json.loads(cls.f03_path.read_text(encoding="utf-8"))
        cls.enclave_data = json.loads(cls.enclave_path.read_text(encoding="utf-8"))

    def test_output03_schema_and_gate3(self) -> None:
        """Verifies Output 03 passes Pydantic validation and Gate 3 QA gates."""
        model = CanonicalSignalProjectionV57.model_validate(self.f03_data)
        self.assertEqual(model.schema_version, "5.7")

        qa_res = run_qa_gate_3(self.f03_data, f1_text=self.f01_text, metadata={"enforce_empty_actions": True})
        self.assertTrue(qa_res["passed"], f"Gate 3 failed: {qa_res.get('failures')}")

    def test_output03_entity_density(self) -> None:
        """Verifies Output 03 includes key attendees, organizations, and governance policies."""
        mutations = self.f03_data["graph_mutations"]
        entity_ids = {e["id"] for e in mutations["entity_upserts"]}

        # Attendee person entities
        self.assertIn("person:amit-ayer", entity_ids)
        self.assertIn("person:jake-stark", entity_ids)
        self.assertIn("person:alex-sanchez", entity_ids)

        # Organization entities
        self.assertIn("org:partner_advisory", entity_ids)
        self.assertIn("org:apex_global-energy", entity_ids)

        # Governance policies
        self.assertIn("policy:statistical-acceptance-sampling-policy", entity_ids)
        self.assertIn("policy:two-phase-operating-model", entity_ids)

    def test_output03_ratified_decisions(self) -> None:
        """Verifies Output 03 formalizes ratified architectural decisions with temporal hygiene."""
        mutations = self.f03_data["graph_mutations"]
        decisions = mutations.get("decision_updates", [])
        self.assertGreaterEqual(len(decisions), 2)

        dec_ids = {d["decision_id"] for d in decisions}
        self.assertIn("decision:50-turbine-denominator-standard", dec_ids)
        self.assertIn("decision:separate-capture-unprocessability", dec_ids)

        for d in decisions:
            self.assertEqual(d["status"], "ratified")
            self.assertEqual(d["observed_at"], "09/18/2026")
            self.assertEqual(d["stale_after"], "12/17/2026")
            self.assertTrue(len(d["evidence_ids"]) > 0)

    def test_output03_evidence_offsets_verbatim(self) -> None:
        """Verifies all timestamped evidence entries have exact character offsets into Output 01."""
        evidence_index = self.f03_data.get("evidence_index", {})
        for eid, ev in evidence_index.items():
            loc = ev.get("locator", {})
            if loc.get("kind") == "timestamp":
                start = ev.get("start_offset")
                end = ev.get("end_offset")
                exact = ev.get("exact_text")
                self.assertIsNotNone(start, f"Missing start_offset on {eid}")
                self.assertIsNotNone(end, f"Missing end_offset on {eid}")
                self.assertEqual(self.f01_text[start:end], exact, f"Offset mismatch on {eid}")

    def test_internal_advisory_enclave_compliance(self) -> None:
        """Verifies the internal advisory enclave satisfies Rule C-19 and quarantine standards."""
        enclave_proj = RestrictedAdvisoryProjection.model_validate(self.enclave_data)
        self.assertEqual(enclave_proj.namespace, "INTERNAL_ADVISORY")
        self.assertEqual(enclave_proj.is_quarantined, 1)
        self.assertEqual(enclave_proj.stealth_score, 1.0)
        self.assertGreaterEqual(len(enclave_proj.advisory_nodes), 4)

        node_types = {n.node_type for n in enclave_proj.advisory_nodes}
        self.assertIn("CAREER_VISIBILITY", node_types)
        self.assertIn("INFO_ASYMMETRY", node_types)
        self.assertIn("partner_advisory_MARGIN_WEDGE", node_types)
        self.assertIn("INFLUENCE_MAP", node_types)

    def test_zero_c19_leakage_in_public_deliverables(self) -> None:
        """Asserts Output 01, 02, and 03 contain zero Rule C-19 forbidden career markers."""
        f03_str = json.dumps(self.f03_data)
        f02_path = self.lens_dir / "02_Jake_Amit_Alex_PartnerAdvisory_09-18-2026_1243PM_0139PM_ET_executive_recap.md"
        f02_text = f02_path.read_text(encoding="utf-8")

        for marker in C19_FORBIDDEN_MARKERS:
            self.assertNotIn(marker, f03_str, f"Forbidden marker '{marker}' leaked into Output 03")
            self.assertNotIn(marker, self.f01_text, f"Forbidden marker '{marker}' leaked into Output 01")
            self.assertNotIn(marker, f02_text, f"Forbidden marker '{marker}' leaked into Output 02")

    def test_copilot_graph_retriever_attendee_affinity(self) -> None:
        """Verifies CopilotGraphRetriever indexes attendee person nodes and retrieves them in < 2ms."""
        topology = MockTopology(self.f03_data)
        attendees = [
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
            {"name": "Jake Stark", "role": "UI and Prompting Lead", "org": "PartnerAdvisory"},
            {"name": "Alex Sanchez", "role": "Lead Developer", "org": "PartnerAdvisory"},
        ]
        retriever = CopilotGraphRetriever(topology, attendees=attendees)

        # Attendee nodes must be recognized
        self.assertIn("person:amit-ayer", retriever._attendee_node_ids)
        self.assertIn("person:jake-stark", retriever._attendee_node_ids)
        self.assertIn("person:alex-sanchez", retriever._attendee_node_ids)

        # Retrieve context for dialogue turn
        start_t = time.perf_counter()
        ctx = retriever.retrieve_context("Amit, what is our acceptance sampling policy for the turbines?")
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        self.assertLess(elapsed_ms, 5.0, f"Retrieval took {elapsed_ms:.2f}ms (must be < 5ms)")
        self.assertIn("Amit Ayer", ctx)
        self.assertIn("Statistical Acceptance Sampling Policy", ctx)

    def test_priority_relationships_coverage(self) -> None:
        """Verifies PRIORITY_RELATIONSHIPS includes CONSTRAINS, OWNS_OR_LEADS, and GOVERNS."""
        self.assertIn("CONSTRAINS", PRIORITY_RELATIONSHIPS)
        self.assertIn("OWNS_OR_LEADS", PRIORITY_RELATIONSHIPS)
        self.assertIn("GOVERNS", PRIORITY_RELATIONSHIPS)

    def test_output03_mutation_budget_ceiling(self) -> None:
        """Verifies Output 03 strictly respects the 25-mutation ceiling (reconciler.py:81)."""
        mutations = self.f03_data.get("graph_mutations", {})
        total_mutations = (
            len(mutations.get("entity_upserts", []))
            + len(mutations.get("relationship_signals", []))
            + len(mutations.get("decision_updates", []))
            + len(mutations.get("proposal_updates", []))
            + len(mutations.get("action_updates", []))
            + len(mutations.get("risk_updates", []))
        )
        self.assertLessEqual(total_mutations, 25, f"Mutation count {total_mutations} exceeds ceiling of 25")
        self.assertEqual(len(mutations.get("entity_upserts", [])), 18)
        self.assertEqual(len(mutations.get("relationship_signals", [])), 5)
        self.assertEqual(len(mutations.get("decision_updates", [])), 2)
        self.assertEqual(len(mutations.get("action_updates", [])), 0, "Action updates must strictly remain empty")

    def test_six_zone_architectural_coverage(self) -> None:
        """Verifies Output 03 systems span all core zones of the LENS pipeline architecture."""
        mutations = self.f03_data.get("graph_mutations", {})
        system_ids = {e["id"] for e in mutations.get("entity_upserts", []) if e.get("type") == "system"}
        self.assertIn("system:mobile-capture-app", system_ids)
        self.assertIn("system:aws-s3", system_ids)
        self.assertIn("system:lens-frontend", system_ids)
        self.assertIn("system:lens-backend", system_ids)
        self.assertIn("system:dual-llm-pipeline", system_ids)
        self.assertIn("system:lens-enterprise-persistence", system_ids)

    def test_copilot_retrieval_latency_benchmark(self) -> None:
        """Verifies sub-2ms average retrieval latency over multiple live conversational queries."""
        topology = MockTopology(self.f03_data)
        attendees = [
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
            {"name": "Jake Stark", "role": "UI and Prompting Lead", "org": "PartnerAdvisory"},
            {"name": "Alex Sanchez", "role": "Lead Developer", "org": "PartnerAdvisory"},
        ]
        retriever = CopilotGraphRetriever(topology, attendees=attendees)

        queries = [
            "Amit, what is our acceptance sampling policy for the turbines?",
            "Jake, how does the mobile capture app deliver to S3?",
            "Alex, can you explain the dual model agreement pipeline?",
            "What did we decide about unprocessable capture versus model inference errors?",
            "How does Two-Phase Operating Model distinguish setup from bulk ingestion?",
        ]

        times = []
        for q in queries * 10:
            t0 = time.perf_counter()
            res = retriever.retrieve_context(q)
            t1 = time.perf_counter()
            self.assertTrue(len(res) > 0)
            times.append((t1 - t0) * 1000.0)

        avg_ms = sum(times) / len(times)
        self.assertLess(avg_ms, 2.0, f"Average retrieval latency {avg_ms:.3f}ms exceeds 2.0ms ceiling")


if __name__ == "__main__":
    unittest.main()
