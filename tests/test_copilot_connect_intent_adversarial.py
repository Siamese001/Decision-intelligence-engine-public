"""
tests/test_copilot_connect_intent_adversarial.py

Adversarial and boundary test suite for Wave 5 Copilot `connect` intent and `gaps`:
1. Boundary payload: Empty input, null tokens, zero-assertion topology.
2. Malformed input: Corrupted structures, unicode surrogates, null bytes.
3. Invariant bypass attempt: C-19 career leverage and personal advisory injection.
4. Concurrency & race safety: Multi-threaded retrieval stress without locks or corruptions.
5. Contradiction gap precision: Strict differentiation between conflicting vs agreeing claims.
"""
from __future__ import annotations

import concurrent.futures
import unittest
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot_connect import (
    GapsResult,
    find_contradiction_gaps,
    get_connected_chains,
)
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


class TestCopilotConnectIntentAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_topology = MagicMock()
        self.mock_topology.graph_sha256 = "sha256_mock_adv_connect"
        self.mock_topology.generation = 5

        self.mock_topology.nodes = {
            "workstream_scada": {
                "canonical_name": "SCADA Modernization",
                "display_name": "SCADA",
                "node_type": "workstream",
                "org": "Apex Global",
            },
            "system_pi_gateway": {
                "canonical_name": "PI Historian Gateway",
                "display_name": "PI Gateway",
                "node_type": "system",
                "org": "Apex Global",
            },
            "vendor_pano_ai": {
                "canonical_name": "Pano AI Early Wildfire Detection",
                "display_name": "Pano AI",
                "node_type": "vendor",
                "org": "Pano AI",
            },
            # C-19 quarantined node
            "c19_secret_node": {
                "canonical_name": "$1.2M Compensation Strategy",
                "display_name": "Executive Comp",
                "node_type": "person",
                "namespace": "INTERNAL_ADVISORY",
                "is_quarantined": 1,
            },
        }

        self.mock_topology.assertions = {
            "ast_scada_pi": {
                "assertion_id": "ast_scada_pi",
                "subject_node_id": "workstream_scada",
                "predicate": "DEPENDS_ON",
                "object_node_id": "system_pi_gateway",
                "object_literal": "PI Historian Gateway",
                "claim_text": "SCADA Modernization depends on PI Historian Gateway.",
                "observed_at": "2026-10-01",
                "source_date": "2026-10-01",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_1"],
                "source_ids": ["src_1"],
            },
            "ast_pi_vendor": {
                "assertion_id": "ast_pi_vendor",
                "subject_node_id": "system_pi_gateway",
                "predicate": "USES_PATTERN",
                "object_node_id": "vendor_pano_ai",
                "object_literal": "Pano AI",
                "claim_text": "PI Historian Gateway uses pattern with Pano AI.",
                "observed_at": "2026-10-02",
                "source_date": "2026-10-02",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_2"],
                "source_ids": ["src_2"],
            },
            # Contradiction: same subject & predicate, different object
            "ast_scada_pi_conflict": {
                "assertion_id": "ast_scada_pi_conflict",
                "subject_node_id": "workstream_scada",
                "predicate": "DEPENDS_ON",
                "object_node_id": "system_cloud_bypass",
                "object_literal": "Direct Cloud Ingress Bypass",
                "claim_text": "SCADA Modernization depends on Direct Cloud Ingress Bypass.",
                "observed_at": "2026-10-05",
                "source_date": "2026-10-05",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_3"],
                "source_ids": ["src_3"],
            },
            # Agreement: same subject, predicate, and object across dates
            "ast_pi_vendor_agree": {
                "assertion_id": "ast_pi_vendor_agree",
                "subject_node_id": "system_pi_gateway",
                "predicate": "USES_PATTERN",
                "object_node_id": "vendor_pano_ai",
                "object_literal": "Pano AI",
                "claim_text": "PI Historian Gateway uses pattern with Pano AI confirmed.",
                "observed_at": "2026-10-05",
                "source_date": "2026-10-05",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_4"],
                "source_ids": ["src_3"],
            },
            # C-19 Quarantined assertion
            "ast_c19_leak": {
                "assertion_id": "ast_c19_leak",
                "subject_node_id": "workstream_scada",
                "predicate": "DELIVERS",
                "object_node_id": "c19_secret_node",
                "object_literal": "Personal Career Leverage and executive compensation band",
                "claim_text": "SCADA Modernization delivers personal career leverage and executive compensation band.",
                "observed_at": "2026-10-01",
                "source_date": "2026-10-01",
                "namespace": "INTERNAL_ADVISORY",
                "is_quarantined": 1,
                "evidence_ids": ["ev_c19"],
                "source_ids": ["src_c19"],
            },
        }

        chains = [
            {
                "seed_id": "workstream_scada",
                "target_id": "vendor_pano_ai",
                "hops": 2,
                "path": ["workstream_scada", "system_pi_gateway", "vendor_pano_ai"],
                "predicates": ["DEPENDS_ON", "USES_PATTERN"],
                "chain_str": "workstream_scada -[DEPENDS_ON]-> system_pi_gateway -[USES_PATTERN]-> vendor_pano_ai",
                "is_priority": True,
                "last_claim": "PI Historian Gateway uses pattern with Pano AI.",
            },
        ]
        self.neighborhood_store = CopilotNeighborhoodStore(chains=chains, status="SUCCESS")

        self.retriever = CopilotGraphRetriever(
            topology=self.mock_topology,
            attendees=[{"name": "harper Hoffman", "role": "Lead", "org": "PartnerAdvisory"}],
            graph_sha256="sha256_mock_adv_connect",
            generation=5,
            neighborhood_store=self.neighborhood_store,
        )

    def test_boundary_empty_and_null_dialogue(self) -> None:
        """Category 1 (Boundary payload): Empty/null inputs fail closed without throwing."""
        for empty_val in ("", "   ", None, {}):
            chains, assertions = self.retriever.get_connected_chains(empty_val)
            self.assertEqual(chains, [])
            self.assertEqual(assertions, [])

            gaps_res = self.retriever.get_gaps(empty_val)
            self.assertIsInstance(gaps_res, GapsResult)
            self.assertEqual(gaps_res.assertions, [])
            self.assertFalse(gaps_res.has_prior_record)
            self.assertEqual(gaps_res.conflicts, [])

    def test_malformed_input_corrupted_structures(self) -> None:
        """Category 2 (Malformed input): Null bytes, invalid types, unparseable objects handled gracefully."""
        corrupted_inputs = [
            "\x00\x00\x00\x01\xff",
            {"weird_key": None, 12345: True},
            object(),
            "   \n\t\r   ",
        ]
        for bad_input in corrupted_inputs:
            chains, assertions = get_connected_chains(self.retriever, bad_input)
            self.assertEqual(chains, [])
            self.assertEqual(assertions, [])

            gaps = find_contradiction_gaps(self.retriever, bad_input)
            self.assertEqual(gaps.conflicts, [])

    def test_c19_career_firewall_quarantine_enforcement(self) -> None:
        """Category 3 (Invariant bypass attempt): C-19 tokens and quarantined nodes stripped from chains and gaps."""
        # 1. Dialogue containing C-19 injection
        c19_dialogue = "Let us discuss the executive compensation band and personal career leverage in SCADA."
        chains, assertions = self.retriever.get_connected_chains(c19_dialogue)

        # Output assertions must not contain C-19 markers
        for a in assertions:
            self.assertFalse(a.get("namespace") == "INTERNAL_ADVISORY")
            self.assertFalse(a.get("is_quarantined") == 1)
            self.assertNotIn("$1.2M", str(a.get("claim_text", "")))
            self.assertNotIn("career_leverage", str(a.get("claim_text", "")).lower())

        for c in chains:
            self.assertNotIn("$1.2M", c.get("chain_str", ""))
            self.assertNotIn("career_leverage", c.get("chain_str", "").lower())

        # 2. Gaps on C-19 injection must not surface quarantined assertions
        gaps_res = self.retriever.get_gaps(c19_dialogue)
        for a in gaps_res.assertions:
            self.assertNotEqual(a.get("assertion_id"), "ast_c19_leak")
            self.assertNotIn("$1.2m", str(a.get("claim_text", "")).lower())

    def test_concurrency_multi_threaded_safety(self) -> None:
        """Category 4 (Concurrency & race): Parallel worker calls do not corrupt retriever or deadlock."""
        def worker(idx: int) -> int:
            query = f"SCADA Modernization and PI Historian Gateway turn {idx}"
            c, a = self.retriever.get_connected_chains(query)
            g = self.retriever.get_gaps(query)
            return len(c) + len(a) + len(g.assertions)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(24)]
            results = [f.result(timeout=5.0) for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 24)
        for count in results:
            self.assertGreaterEqual(count, 0)

    def test_contradiction_gap_precision(self) -> None:
        """Category 5 (Contradiction precision): Conflicting claims yield conflicts; agreeing claims do not."""
        query = "SCADA Modernization and PI Historian Gateway"
        res = self.retriever.get_gaps(query)

        self.assertTrue(res.has_prior_record)
        self.assertGreaterEqual(len(res.conflicts), 1)

        # 1. Exactly one conflict item for DEPENDS_ON on workstream_scada
        depends_conflicts = [c for c in res.conflicts if c["predicate"] == "DEPENDS_ON"]
        self.assertEqual(len(depends_conflicts), 1)
        conflict = depends_conflicts[0]
        self.assertIn("PI Historian Gateway", conflict["claim_a"])
        self.assertIn("Direct Cloud Ingress Bypass", conflict["claim_b"])

        # 2. Zero conflict items for USES_PATTERN because Pano AI agrees across both dates
        pattern_conflicts = [c for c in res.conflicts if c["predicate"] == "USES_PATTERN"]
        self.assertEqual(len(pattern_conflicts), 0)


if __name__ == "__main__":
    unittest.main()
