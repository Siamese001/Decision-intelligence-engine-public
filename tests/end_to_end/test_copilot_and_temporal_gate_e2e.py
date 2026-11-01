"""
End-to-end verification for Wave 5: Copilot Live Fallback Parity & Temporal Gate Full-Scan Resolution.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot_intents import eval_respond
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.query_router_core.temporal_gate import (
    extract_temporal_target_date,
    scan_unpromoted_dlq_and_worktrees,
    verify_temporal_source_existence,
)


class _DummyDialogue:
    def __init__(self, turns: list[dict[str, str]], raw_request: str = "") -> None:
        self._turns = turns
        self._raw_request = raw_request

    def focus_turns(self) -> list[dict[str, str]]:
        return list(self._turns)

    def retrieval_seeds(self) -> list[str]:
        return []


class TestCopilotAndTemporalGateE2E(unittest.TestCase):
    def test_e2e_eval_respond_supplements_substantive_graph_assertions_with_live_turns(self) -> None:
        class _Topology:
            def __init__(self) -> None:
                self.nodes = {
                    "person:ninan": {
                        "node_id": "person:ninan",
                        "canonical_name": "Speaker_Beta",
                        "display_name": "Speaker_Beta",
                        "node_type": "person",
                        "organization": "Apex Global",
                        "role": "Executive Sponsor",
                    },
                    "workstream:sap": {
                        "node_id": "workstream:sap",
                        "canonical_name": "SAP Pipeline",
                        "display_name": "SAP Pipeline",
                        "node_type": "workstream",
                    },
                }
                self.assertions = {
                    "asrt_graph_1": {
                        "assertion_id": "asrt_graph_1",
                        "subject_node_id": "person:ninan",
                        "object_node_id": "workstream:sap",
                        "predicate": "COMMITTED_TO",
                        "claim_text": "Speaker_Beta committed to reviewing the SAP cutover checklist.",
                        "confidence_score": 0.94,
                        "engagement_id": "project:lens",
                        "primary_source_id": "src_1",
                    }
                }

        retriever = CopilotGraphRetriever(
            topology=_Topology(),
            graph_sha256="c" * 64,
            generation=1,
            attendees=[{"name": "Speaker_Beta", "role": "Executive Sponsor", "org": "Apex Global"}],
            engagement_id="project:lens",
        )
        dialogue = _DummyDialogue(
            turns=[
                {
                    "seq": "1",
                    "speaker": "Speaker_Beta",
                    "text": "We must validate the Maximo disaster recovery runbook before Friday.",
                    "timestamp": "00:01:15",
                }
            ]
        )
        body, assertions, ok = eval_respond(retriever, "Speaker_Beta", dialogue)
        self.assertTrue(ok)
        aids = [a.get("assertion_id") for a in assertions]
        self.assertIn("asrt_graph_1", aids)
        self.assertTrue(any(str(aid).startswith("live_turn_") for aid in aids))
        self.assertIn("Speaker_Beta committed to reviewing the SAP cutover checklist.", body)
        self.assertIn("validate the Maximo disaster recovery runbook", body)

    def test_e2e_temporal_gate_resolves_2hop_speaker_and_full_dlq_scan(self) -> None:
        self.assertEqual(extract_temporal_target_date("Recap the 10/19/26 architecture review"), "2026-10-19")
        self.assertEqual(extract_temporal_target_date("What did we decide on 19 Oct 2026?"), "2026-10-19")

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE source (source_id TEXT, source_name TEXT, source_date TEXT, cold_archive_ref TEXT)")
        conn.execute("CREATE TABLE node (node_id TEXT, canonical_name TEXT)")
        conn.execute(
            "CREATE TABLE assertion (assertion_id TEXT, primary_source_id TEXT, subject_node_id TEXT, object_node_id TEXT, claim_text TEXT)"
        )
        # Source name does NOT mention 'Speaker_Alpha', but 2-hop assertion/node does
        conn.execute(
            "INSERT INTO source VALUES ('src_1019', 'Weekly Architecture Sync', '2026-10-19', 'output/nea/10-19-2026/Weekly_Sync')"
        )
        conn.execute("INSERT INTO node VALUES ('person:brien', 'Speaker_Alpha McPhee')")
        conn.execute(
            "INSERT INTO assertion VALUES ('asrt_1', 'src_1019', 'person:brien', 'system:gis', 'Speaker_Alpha McPhee approved GIS migration')"
        )

        res = verify_temporal_source_existence("What did Speaker_Alpha McPhee approve on 10/19/26?", conn=conn)
        self.assertEqual(res["status"], "FOUND_IN_KNOWLEDGE_GRAPH")
        self.assertEqual(len(res["matching_sources"]), 1)
        self.assertEqual(res["matching_sources"][0]["source_id"], "src_1019")


if __name__ == "__main__":
    unittest.main()
