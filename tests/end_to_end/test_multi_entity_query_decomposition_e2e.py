#!/usr/bin/env python3
"""E2E test for multi-entity query decomposition, dynamic resolution, live context, and current_state rendering."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.query import (
    compile_query_coverage_manifest,
    exact_retrieval,
    render_markdown,
)
from dual_pipeline.query_core.live_context import (
    extract_transcript_cache_turns,
    resolve_live_context_for_entities,
)
from dual_pipeline.query_core.planning import build_query_plan
from dual_pipeline.query_core.reasoning import reason_over_packet
from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query_router import QueryDestination
from tools.mcp_test_orchestrator_server import provision_isolated_test_db


class TestMultiEntityQueryDecompositionE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="multi_entity_e2e_")
        self.root = Path(self.tmp.name)
        res = json.loads(provision_isolated_test_db())
        self.db_path = Path(res["isolated_db_path"])

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    display_name TEXT,
                    node_type TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_node_id TEXT,
                    canonical_status TEXT NOT NULL,
                    validation_state TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    classification TEXT NOT NULL,
                    allowed_audiences_json TEXT NOT NULL,
                    qualifiers_json TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    PRIMARY KEY (assertion_id, evidence_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS search_document (
                    search_document_id INTEGER PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    canonical_status TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    assertion_id TEXT NOT NULL
                )"""
            )

            # Seed multi-entity nodes
            nodes = [
                ("stakeholder_lori_bartlett", "Lori Bartlett", "Lori Bartlett", "stakeholder", 1),
                ("entity-7b411db442f64e7e59c6190cf2f2", "Noemi Monnerville", "Noemi Monnerville", "person", 1),
                ("workstream_pd_conduit", "PD Conduit / Conductor", "PD Conduit", "workstream", 1),
                ("org_florida_power", "Apex Power & Grid", "Florida Power", "organization", 1),
                ("meeting_sprint_review", "Sprint Review", "Sprint Review", "meeting", 1),
            ]
            conn.executemany(
                """INSERT OR REPLACE INTO node (node_id, canonical_name, display_name, node_type, active)
                   VALUES (?, ?, ?, ?, ?)""",
                nodes,
            )

            # Seed assertions
            assertions = [
                (
                    "asrt-lori-conduit-pause",
                    "stakeholder_lori_bartlett",
                    "PAUSED",
                    "workstream_pd_conduit",
                    "canonical",
                    "approved",
                    "current",
                    1,
                    "internal",
                    '["ENGINEERING"]',
                    '{}',
                ),
                (
                    "asrt-noemi-knowledge-mgmt",
                    "entity-7b411db442f64e7e59c6190cf2f2",
                    "LEADS",
                    "workstream_knowledge_mgmt",
                    "canonical",
                    "approved",
                    "current",
                    1,
                    "internal",
                    '["ENGINEERING"]',
                    '{}',
                ),
                (
                    "asrt-florida-power-assertion",
                    "org_florida_power",
                    "OPERATES_IN",
                    "state_florida",
                    "canonical",
                    "approved",
                    "current",
                    1,
                    "internal",
                    '["ENGINEERING"]',
                    '{}',
                ),
                (
                    "asrt-sprint-review-assertion",
                    "meeting_sprint_review",
                    "HELD_ON",
                    "date_20261016",
                    "canonical",
                    "approved",
                    "current",
                    1,
                    "internal",
                    '["ENGINEERING"]',
                    '{}',
                ),
            ]
            conn.executemany(
                """INSERT OR REPLACE INTO assertion
                   (assertion_id, subject_node_id, predicate, object_node_id, canonical_status,
                    validation_state, currentness_state, retrieval_eligible, classification,
                    allowed_audiences_json, qualifiers_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                assertions,
            )

            # Seed assertion evidence linking
            conn.executemany(
                """INSERT OR REPLACE INTO assertion_evidence (assertion_id, evidence_id)
                   VALUES (?, ?)""",
                [
                    ("asrt-lori-conduit-pause", "ev-1"),
                    ("asrt-noemi-knowledge-mgmt", "ev-2"),
                    ("asrt-florida-power-assertion", "ev-3"),
                    ("asrt-sprint-review-assertion", "ev-4"),
                ],
            )

            # Seed search_document rows
            search_docs = [
                (
                    1,
                    "assertion",
                    "asrt-lori-conduit-pause",
                    "Lori Bartlett debrief on P&C Conduit pause",
                    "Lori confirmed P&C Conduit was paused on Aug 18 pending resource review.",
                    "canonical",
                    "current",
                    1,
                    "src-1",
                    "asrt-lori-conduit-pause",
                ),
                (
                    2,
                    "assertion",
                    "asrt-noemi-knowledge-mgmt",
                    "Noemi Monnerville view on knowledge management",
                    "Noemi Monnerville discussed NEA knowledge management and journey documentation.",
                    "canonical",
                    "current",
                    1,
                    "src-2",
                    "asrt-noemi-knowledge-mgmt",
                ),
                (
                    3,
                    "assertion",
                    "asrt-florida-power-assertion",
                    "Apex Power & Grid utility infrastructure",
                    "Apex Power & Grid operational footprint in South Florida.",
                    "canonical",
                    "current",
                    1,
                    "src-3",
                    "asrt-florida-power-assertion",
                ),
                (
                    4,
                    "assertion",
                    "asrt-sprint-review-assertion",
                    "Sprint Review sprint demo notes",
                    "Sprint Review demo notes covering biometric ingestion and review checkpoints.",
                    "canonical",
                    "current",
                    1,
                    "src-4",
                    "asrt-sprint-review-assertion",
                ),
            ]
            conn.executemany(
                """INSERT OR REPLACE INTO search_document
                   (search_document_id, object_type, object_id, title, body, canonical_status,
                    currentness_state, retrieval_eligible, source_id, assertion_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                search_docs,
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_e2e_first_name_resolution_and_word_boundary_guards(self) -> None:
        """First names resolve unambiguously without false-positive hits on Florida or Review."""
        question = "Lori debrief on P&C Conduit pause and Noemi view"
        exact = exact_retrieval(
            question,
            self.db_path,
            classification="internal",
            audiences=["ENGINEERING"],
            retrieval_mode="factual",
        )

        # 1. Unambiguous first-name resolution
        matched_node_ids = {m["node_id"] for m in exact["entity_matches"]}
        self.assertIn("stakeholder_lori_bartlett", matched_node_ids)
        self.assertIn("entity-7b411db442f64e7e59c6190cf2f2", matched_node_ids)
        self.assertIn("workstream_pd_conduit", matched_node_ids)

        # 2. Substring collision guards
        self.assertNotIn("org_florida_power", matched_node_ids)
        self.assertNotIn("meeting_sprint_review", matched_node_ids)

        # 3. Exact anchors must not match Florida Power for 'lori' or Sprint Review for 'view'
        retrieved_doc_ids = {a["search_document_id"] for a in exact["exact_anchors"]}
        self.assertIn(1, retrieved_doc_ids)  # Lori Conduit
        self.assertIn(2, retrieved_doc_ids)  # Noemi
        self.assertNotIn(3, retrieved_doc_ids)  # Florida Power should NOT match 'lori'
        self.assertNotIn(4, retrieved_doc_ids)  # Sprint Review should NOT match 'view'

    def test_e2e_coverage_manifest_distinct_named_subjects(self) -> None:
        """Multi-entity queries register distinct named_subject items without falling back to NEE."""
        question = "Lori debrief on P&C Conduit pause and Noemi view"
        dest_artifact = {
            "canonical_digest": {"value": "d" * 64},
            "delivery_profile": "internal_technical",
            "artifact_type": "answer",
            "materiality": "medium",
            "destination": QueryDestination.GRAPH_EVIDENCE_UTILITY.value,
        }

        active = {
            "graph_path": str(self.db_path),
            "graph_snapshot": "snap-001",
            "graph_sha256": "a" * 64,
        }

        plan = build_query_plan(
            question=question,
            query_type="current_state_corroboration",
            request_id="req-e2e-001",
            run_id="run-e2e-001",
            current_trace_id="a" * 32,
            l2_config=load_l2_config(),
            provider_egress_allowed=False,
            destination_decision=dest_artifact,
            assurance_profile=None,
            graph_path=self.db_path,
        )

        manifest = compile_query_coverage_manifest(
            question=question,
            validated_request_digest="b" * 64,
            plan=plan,
            active=active,
            authorization_scope_digest="c" * 64,
        )

        named_subjects = [
            req["requirement_label"]
            for req in manifest["requirements"]
            if req["requirement_kind"] == "named_subject"
        ]
        self.assertIn("Lori Bartlett", named_subjects)
        self.assertIn("Noemi Monnerville", named_subjects)
        self.assertIn("PD Conduit / Conductor", named_subjects)
        self.assertNotIn("NEE", named_subjects)

    def test_e2e_reasoning_and_current_state_markdown_rendering(self) -> None:
        """current_state is rendered under ## Current state in markdown without cross-attribution."""
        plan = {
            "query_type": "current_state_corroboration",
            "primary_entities": ["Lori Bartlett", "Noemi Monnerville"],
            "materiality": "medium",
        }
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-lori-pause",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "Lori Bartlett on Conduit pause",
                        "retrieval_text": "Lori confirmed Conduit pause on Aug 18.",
                        "evidence_ids": ["ev-1"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                },
                {
                    "record": {
                        "record_id": "rec-noemi-km",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "Noemi Monnerville on KM",
                        "retrieval_text": "Noemi discussed NEA knowledge management framework.",
                        "evidence_ids": ["ev-2"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                },
            ],
            "candidate": [],
            "counterevidence": [],
            "gaps": [],
        }

        answer = reason_over_packet(plan, packet)
        self.assertTrue(len(answer["current_state"]) >= 2)
        statements = [c["statement"] for c in answer["current_state"]]
        self.assertTrue(any("Lori confirmed Conduit pause" in s for s in statements))
        self.assertTrue(any("Noemi discussed NEA knowledge management" in s for s in statements))

        # Verify markdown rendering
        md = render_markdown(answer, "X3A_APPROVED")
        self.assertIn("## Current state", md)
        self.assertIn("Lori confirmed Conduit pause", md)
        self.assertIn("Noemi discussed NEA knowledge management", md)

    def test_e2e_live_context_extraction_from_cache(self) -> None:
        """Simulated live transcript cache turns are discoverable and parsed."""
        cache_dir = self.root / "output" / "transcript_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / "00_10-16-2026_Noemi_Monnerville_Amit_1_1_test.transcript_cache.json"
        sample_transcript = (
            "**Speaker 1** [00:00:48]: I do think that there's, just in general, we have an issue with knowledge management.\n\n"
            "**Speaker 1** [00:01:10]: Because I think the problem is everybody has it up here, and we don't actually have it documented for people to rally around.\n\n"
            "**Speaker 2** [00:01:34]: I agree on the documentation point.\n"
        )
        cache_file.write_text(sample_transcript, encoding="utf-8")

        turns = extract_transcript_cache_turns(cache_file)
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[0]["speaker"], "Speaker 1")
        self.assertIn("knowledge management", turns[0]["text"])

        live_records = resolve_live_context_for_entities(["Noemi Monnerville"], repo_root=self.root)
        self.assertTrue(len(live_records) >= 1)
        self.assertIn("Live Transcript Cache", live_records[0]["title"])
        self.assertIn("knowledge management", live_records[0]["retrieval_text"])


if __name__ == "__main__":
    unittest.main()
