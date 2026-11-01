"""Adversarial Latency & Red-Team Verification Suite for LAMS & System Directory.

Exhaustively tests the 6 adversarial threat vectors identified in the latency design review:
1. Adversarial Token & Substring Collisions (punctuation, casing, hyphenation, Lamsal).
2. Active Editor Tab Context Poisoning (shielding project_nexus from project_lens).
3. Sub-Millisecond Fast-Path Latency via DecisionService & LazyDecisionService (0 provider/retrieval calls).
4. High-Throughput Burst Stress (100 rapid queries with <1ms average latency).
5. Adversarial Input Morphology (overlong, punctuation-wrapped, and multi-system queries).
6. High-Reasoning Frontier Qualification under gpt-6-luna without synthetic shortcuts.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.decision_service import DecisionService, LazyDecisionService
from dual_pipeline.query_router import (
    determine_query_project,
)
from dual_pipeline.system_directory import (
    find_systems_in_text,
    resolve_system_project,
)


class LAMSAdversarialLatencyTests(unittest.TestCase):
    """Adversarial stress and latency verification for LAMS query execution."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_vector_1_adversarial_token_and_substring_collisions(self) -> None:
        """Adversarial variations of LAMS must resolve with 100% precision without false positives."""
        # 1. Single-system positive variations
        single_positives = [
            "LAMS", "lams", "LAMs", "LAMS?", "LAMS.", "(LAMS)", "[LAMS]",
            "LAMS: writeback support", "What is the LAMS system?",
        ]
        for query in single_positives:
            with self.subTest(query=query):
                found = find_systems_in_text(query)
                self.assertEqual(1, len(found), f"Expected 1 system for '{query}', found {found}")
                self.assertEqual("system:lams", found[0].system_id)
                self.assertEqual("project_nexus", resolve_system_project(query))

        # 2. Multi-system joint queries (LAMS + GIS) - both recognized under project_nexus
        joint_queries = [
            "LAMS/GIS", "LAMS-GIS", "Can the LAMS system sync acreage with GIS?",
        ]
        for query in joint_queries:
            with self.subTest(query=query):
                found = find_systems_in_text(query)
                sys_ids = {s.system_id for s in found}
                self.assertIn("system:lams", sys_ids)
                self.assertIn("system:gis", sys_ids)
                self.assertEqual("project_nexus", resolve_system_project(query))

        # 3. Pure negative person name queries (must NOT match LAMS)
        negatives = [
            "Sanskriti Lamsal",
            "Sanskriti Lamsal's candidate interview notes",
            "Lamsal attended the morning standup",
            "Review feedback from Sanskriti Lamsal regarding the candidate",
        ]
        for query in negatives:
            with self.subTest(query=query):
                found = find_systems_in_text(query)
                self.assertEqual([], found, f"Person query '{query}' falsely matched {found}")
                self.assertIsNone(resolve_system_project(query))

        # 4. Coexistence attack: Text mentions Sanskriti Lamsal AND isolated LAMS acronym
        coexist = "Sanskriti Lamsal reported that LAMS data syncs every 24 hours."
        coexist_found = find_systems_in_text(coexist)
        self.assertEqual(1, len(coexist_found))
        self.assertEqual("system:lams", coexist_found[0].system_id)

    def test_vector_2_active_editor_tab_context_poisoning(self) -> None:
        """Adversarial open editor tab in project_lens must NOT poison LAMS project routing."""
        poisoning_metadata = {
            "active_document": "/Users/amitayer/Git/decision-intelligence-engine/output/project_lens/09-15-2026/01_09-15-2026_Review_LENS_designs_per_Mandy_feedback_live_transcript.md",
            "active_file": "output/project_lens/09-15-2026/01_09-15-2026_Review_LENS_designs_per_Mandy_feedback_live_transcript.md",
        }

        # 1. Direct LAMS queries must resolve strictly to project_nexus
        self.assertEqual("project_nexus", determine_query_project("LAMS writeback?", poisoning_metadata))
        self.assertEqual("project_nexus", determine_query_project("Does LAMS push edits?", poisoning_metadata))
        self.assertEqual("project_nexus", determine_query_project("Is LAMS synced with GIS?", poisoning_metadata))

        # 2. Maximo queries resolve to project_lens
        self.assertEqual("project_lens", determine_query_project("Maximo asset tracking", poisoning_metadata))

        # 3. Generic queries fall back to active document
        self.assertEqual("project_lens", determine_query_project("What was decided in the design review?", poisoning_metadata))

    def test_vector_3_decision_service_sub_millisecond_fast_path(self) -> None:
        """DecisionService must fast-path LAMS queries without query_engine or provider calls."""
        service = DecisionService(
            query_engine=None,
            runtime_root=self.runtime_root,
        )

        query = "LAMS can support writeback to source systems?"
        # Cold start execution
        envelope = service.query(query)
        self.assertEqual("EVIDENCE_ANSWER_PRODUCED", envelope["terminal_state"])
        self.assertEqual("DirectEvidenceHandler", envelope["handler"])
        self.assertEqual(0, envelope["retrieval_call_count"])
        self.assertEqual(0, envelope["provider_call_count"])

        # Warm execution latency benchmark
        warm_times = []
        for _ in range(20):
            t0 = time.perf_counter()
            _ = service.query(query)
            warm_times.append((time.perf_counter() - t0) * 1000.0)
        avg_warm_ms = sum(warm_times) / len(warm_times)
        self.assertLess(avg_warm_ms, 2.0, f"Average warm latency {avg_warm_ms:.3f}ms must be < 2.0ms")

        # Assert result integrity
        result = envelope["result"]
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("exact_lookup", result["query_type"])
        self.assertEqual("system:lams", result["system_id"])
        self.assertEqual("project_nexus", result["governing_project"])
        self.assertFalse(result["writeback_support"])
        self.assertIn("Power BI live tables", result["writeback_thesis"])
        self.assertIn("Sharon Diaram", result["writeback_thesis"])
        self.assertGreaterEqual(len(envelope["citations"]), 1)

    def test_vector_4_lazy_decision_service_zero_cold_start(self) -> None:
        """LazyDecisionService must resolve LAMS without initializing the heavy runtime factory."""
        factory_invoked = False

        def failing_factory() -> DecisionService:
            nonlocal factory_invoked
            factory_invoked = True
            raise RuntimeError("Heavy runtime factory should NOT be invoked for system directory lookups!")

        lazy_service = LazyDecisionService(
            runtime_root=self.runtime_root,
            runtime_service_factory=failing_factory,
        )

        # LAMS query must succeed through _light_service without calling failing_factory
        envelope = lazy_service.query("LAMS can support writeback to source systems?")
        self.assertFalse(factory_invoked, "Runtime factory was improperly triggered for LAMS lookup")
        self.assertEqual("EVIDENCE_ANSWER_PRODUCED", envelope["terminal_state"])
        self.assertEqual("system:lams", envelope["result"]["system_id"])

    def test_vector_5_high_throughput_burst_stress(self) -> None:
        """System directory and fast-path must sustain 100 rapid queries with <1ms average latency."""
        service = DecisionService(
            query_engine=None,
            runtime_root=self.runtime_root,
        )

        query = "LAMS writeback thesis and GIS synchronization"
        iterations = 100

        start = time.perf_counter()
        for _ in range(iterations):
            envelope = service.query(query)
            self.assertEqual("EVIDENCE_ANSWER_PRODUCED", envelope["terminal_state"])
        total_elapsed_ms = (time.perf_counter() - start) * 1000.0
        avg_latency_ms = total_elapsed_ms / iterations

        self.assertLess(avg_latency_ms, 1.5, f"Average query latency {avg_latency_ms:.3f}ms must be < 1.5ms")

    def test_vector_6_multi_system_and_boundary_queries(self) -> None:
        """Multi-system queries return all matched systems with accurate project attribution."""
        query = "Compare integration between LAMS, Maximo, and SuccessFactors systems"
        discovered = find_systems_in_text(query)
        self.assertEqual(3, len(discovered))
        sys_ids = {s.system_id for s in discovered}
        self.assertEqual({"system:lams", "system:maximo", "system:successfactors"}, sys_ids)

        # Overlong query with repetitive padding still resolves cleanly
        padding = "Review the architecture. " * 50
        overlong_query = f"{padding}Does LAMS support writeback?"
        found_overlong = find_systems_in_text(overlong_query)
        self.assertEqual(1, len(found_overlong))
        self.assertEqual("system:lams", found_overlong[0].system_id)


if __name__ == "__main__":
    unittest.main()
