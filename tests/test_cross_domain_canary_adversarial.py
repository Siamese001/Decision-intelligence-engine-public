"""Adversarial & Edge-Case Tests for Cross-Domain Canary Reachability, Dynamic Entities & ACL Boundaries."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from dual_pipeline.core import sha256_file
from dual_pipeline.graph_retrieval_core.clearance_cache import authorized_records_cached
from dual_pipeline.l2 import validate_decision_model_output
from dual_pipeline.query import reason_over_packet
from dual_pipeline.query_core.dynamic_entities import (
    resolve_dynamic_entity_matches,
    resolve_named_coverage_subjects,
)
from dual_pipeline.query_core.planning import _resolve_plan_primary_entities
from dual_pipeline.query_core.reasoning import validate_answer


class CrossDomainCanaryAdversarialTests(unittest.TestCase):
    def test_exact_production_regression_non_person_nodes_do_not_match_generic_query_words(self) -> None:
        """Exact production regression: multi-word workstream/activity nodes do not match generic query words like 'next' or 'actions'."""
        with tempfile.TemporaryDirectory(prefix="canary-adv-dyn-") as tmpdir:
            graph = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE canonical_alias (entity_id TEXT, display_alias TEXT, active INTEGER);
                    """
                )
                conn.execute("INSERT INTO node VALUES ('n-nee', 'NEE', 'Apex Global Enterprises', 'Organization', 1)")
                conn.execute("INSERT INTO node VALUES ('n-gh', 'GitHub Actions', 'GitHub Actions', 'System', 1)")
                conn.execute(
                    "INSERT INTO node VALUES ('n-act', 'Activity A4: Mobilize – Define What Comes Next', 'Activity A4', 'Workstream', 1)"
                )
                conn.execute(
                    "INSERT INTO node VALUES ('n-mod', 'HR Modernization', 'HR Modernization', 'Initiative', 1)"
                )
                conn.execute(
                    "INSERT INTO node VALUES ('n-person', 'Jane Doe', 'Jane Doe', 'Person', 1)"
                )
                conn.commit()

                q = "What are the top next best actions and strategic opportunities for NEE grid modernization?"
                matches = resolve_dynamic_entity_matches(conn, q)
                matched_names = [m["canonical_name"] for m in matches]
                self.assertIn("NEE", matched_names)
                self.assertNotIn("GitHub Actions", matched_names)
                self.assertNotIn("Activity A4: Mobilize – Define What Comes Next", matched_names)
                self.assertNotIn("HR Modernization", matched_names)

                # Person given-name matching still works unambiguously
                person_matches = resolve_dynamic_entity_matches(conn, "What did Jane recommend?")
                self.assertEqual(["Jane Doe"], [m["canonical_name"] for m in person_matches])
            finally:
                conn.close()

            primary = _resolve_plan_primary_entities(
                "What are the top next best actions and strategic opportunities for NEE grid modernization?",
                graph,
            )
            self.assertEqual(["NEE"], primary)

    def test_invariant_bypass_public_classification_blocks_internal_consulting_expansion(self) -> None:
        """Invariant bypass attempt: public classification cannot use nee-engineering->internal_consulting expansion to access internal records."""
        with tempfile.TemporaryDirectory(prefix="canary-adv-acl-") as tmpdir:
            tmp = Path(tmpdir)
            graph = tmp / "graph.sqlite"
            sidecar = tmp / "sidecar.sqlite"
            sqlite3.connect(graph).close()

            sconn = sqlite3.connect(sidecar)
            try:
                sconn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT, artifact_json TEXT)")
                artifact = {
                    "record_id": "rec-public-partner_advisory-only",
                    "assertion_id": "ass-public-partner_advisory-only",
                    "record_type": "assertion",
                    "lane": "approved",
                    "classification": "public",
                    "audiences": ["internal_consulting"],
                    "title": "PartnerAdvisory audience record",
                    "retrieval_text": "Restricted to internal_consulting audience even at public classification.",
                    "evidence_ids": ["ev-1"],
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                    "extensions": {"com.partner_advisory.nee.graph-object-id": "ass-public-partner_advisory-only"},
                }
                sconn.execute(
                    "INSERT INTO retrieval_record VALUES (?, ?, ?)",
                    ("rec-public-partner_advisory-only", "approved", json.dumps(artifact)),
                )
                sconn.commit()
            finally:
                sconn.close()

            by_rec, _ = authorized_records_cached(
                sidecar_path=sidecar,
                graph_path=graph,
                allowed_lanes=["approved"],
                retrieval_mode="factual",
                classification="public",
                audiences=["nee-engineering"],
                allowed_assertion_ids={"ass-public-partner_advisory-only"},
                validate_fn=lambda p: {"metadata": {"graph_sha256": sha256_file(graph)}},
            )
            self.assertEqual({}, by_rec)

    def test_invariant_bypass_guidance_citation_in_next_best_actions_is_rejected(self) -> None:
        """Invariant bypass attempt: citing a guidance record in next_best_actions basis_citations is rejected."""
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-fact",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "Approved fact",
                        "retrieval_text": "Approved risk evidence supports the governed opportunity.",
                        "evidence_ids": ["ev-fact"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                    "evidence": [{"evidence_id": "ev-fact"}],
                },
                {
                    "record": {
                        "record_id": "rec-guide",
                        "record_type": "action_item",
                        "lane": "approved",
                        "title": "Approved guidance",
                        "retrieval_text": "Validate the opportunity with the accountable owner.",
                        "evidence_ids": ["ev-guide"],
                    },
                    "retrieval_metadata": {"answer_use": "guidance"},
                    "evidence": [{"evidence_id": "ev-guide"}],
                },
            ],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "gaps": [],
        }
        valid = reason_over_packet({"query_type": "opportunity_nba"}, packet)
        self.assertTrue(
            validate_decision_model_output(
                valid,
                evidence_packet=packet,
                expected_query_type="opportunity_nba",
            ).valid
        )
        forged = deepcopy(valid)
        forged["next_best_actions"] = [
            {
                "action": "Forged guidance-cited action",
                "basis_citations": ["rec-guide"],
            }
        ]
        self.assertFalse(
            validate_decision_model_output(
                forged,
                evidence_packet=packet,
                expected_query_type="opportunity_nba",
            ).valid
        )
        self.assertEqual("FAIL", validate_answer(forged, packet)["status"])

    def test_boundary_and_malformed_inputs_in_dynamic_entity_and_candidate_review(self) -> None:
        """Boundary & malformed input: missing relative graph_path and empty candidate_promotion_review packets degrade safely."""
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        subjects = resolve_named_coverage_subjects(
            "What is the status of PDLC and pdlc?",
            {"primary_entities": ["PDLC", "pdlc"]},
            active={"graph_path": "nonexistent/relative/graph.sqlite"},
        )
        self.assertEqual(["PDLC"], subjects)

        # Verify that when a relative graph_path falls back to resolve_active_graph_path,
        # dynamic entities are resolved from SQLite without raising ImportError.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER)"
            )
            conn.execute(
                "INSERT INTO node VALUES ('node:1', 'zephyr grid', 'Zephyr Grid', 'initiative', 1)"
            )
            conn.commit()
            conn.close()
            with patch("dual_pipeline.runtime_root.resolve_active_graph_path", return_value=db_path):
                resolved_subjects = resolve_named_coverage_subjects(
                    "What is the status of Zephyr Grid?",
                    {},
                    active={"graph_path": "relative/missing/graph.sqlite"},
                )
            self.assertIn("zephyr grid", [s.lower() for s in resolved_subjects])

        empty_packet = {
            "approved": [],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "gaps": ["No records"],
        }
        cand_ans = reason_over_packet(
            {
                "query_type": "candidate_promotion_review",
                "retrieval_mode": "candidate_review",
                "allowed_lanes": ["approved", "candidate"],
                "primary_entities": [],
            },
            empty_packet,
        )
        self.assertEqual([], cand_ans["opportunities"])
        self.assertEqual([], cand_ans["next_best_actions"])


if __name__ == "__main__":
    unittest.main()
