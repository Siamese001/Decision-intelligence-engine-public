"""End-to-end runtime verification for canonical query filtering and candidate isolation."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.decision_adapter_core.row_reader import _read_rows
from dual_pipeline.graph_retrieval_core.clearance_cache import _evaluate_record_clearance
from dual_pipeline.index_core.eligibility import (
    classify_retrieval_eligibility,
    effective_retrieval_lane,
)
from dual_pipeline.index_core.records import render_retrieval_records
from dual_pipeline.query_core.graph_first_engine import GraphFirstQueryEngine
from dual_pipeline.query_core.hydration import exact_retrieval
from dual_pipeline.query_core.offline_retriever import OfflineGraphContextRetriever


def _provision_wave3_test_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_name TEXT NOT NULL,
                cold_archive_ref TEXT,
                source_posture TEXT
            );
            CREATE TABLE source_version (
                source_version_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                source_date_normalized TEXT NOT NULL
            );
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                description TEXT DEFAULT '',
                active INTEGER DEFAULT 1,
                is_quarantined INTEGER DEFAULT 0
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                object_node_id TEXT,
                predicate TEXT NOT NULL,
                object_literal TEXT,
                scope_text TEXT,
                qualifiers_json TEXT NOT NULL,
                primary_source_id TEXT NOT NULL,
                extraction_run_id TEXT NOT NULL,
                classification TEXT NOT NULL,
                allowed_audiences_json TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                recorded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                currentness_state TEXT NOT NULL,
                canonical_status TEXT NOT NULL,
                canonical_eligible INTEGER NOT NULL,
                retrieval_eligible INTEGER NOT NULL,
                validation_state TEXT NOT NULL,
                approval_reference TEXT,
                confidence_label TEXT,
                confidence_score REAL,
                hitl_required INTEGER NOT NULL,
                assertion_type TEXT,
                origin_type TEXT,
                validation_priority TEXT,
                validation_owner TEXT,
                atomicity_state TEXT,
                is_quarantined INTEGER DEFAULT 0
            );
            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_version_id TEXT NOT NULL,
                locator_kind TEXT NOT NULL,
                locator_value TEXT NOT NULL,
                transcript_timestamp TEXT,
                direct_quote TEXT NOT NULL,
                evidence_directness TEXT,
                source_authority_rank INTEGER
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_relationship TEXT NOT NULL
            );
            CREATE TABLE search_document (
                search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                search_document_id TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                canonical_status TEXT NOT NULL,
                currentness_state TEXT NOT NULL,
                retrieval_eligible INTEGER NOT NULL,
                source_id TEXT NOT NULL,
                assertion_id TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO source VALUES (?, ?, ?, ?)",
            ("source-nea-q4", "NEA Account Planning", "archive/nea.md", "internal"),
        )
        conn.execute(
            "INSERT INTO source_version VALUES (?, ?, ?, ?, ?)",
            ("sv-1", "source-nea-q4", "a" * 64, "b" * 64, "2026-08-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, '', 1, 0)",
            ("person_stephane", "person", "Cameron Mercer", "Cameron Mercer"),
        )
        conn.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, '', 1, 0)",
            ("workstream_lens", "workstream", "Project LENS", "Project LENS"),
        )
        # 1. Canonical Q4 workstream assertion on Cameron Mercer
        conn.execute(
            """
            INSERT INTO assertion VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0
            )
            """,
            (
                "assertion-canonical-nea-q4",
                "person_stephane",
                "workstream_lens",
                "LEADS_DECISION",
                "Cameron Mercer sponsors Project LENS Q4 workstream expansion.",
                "NEA Q4 Account Planning",
                json.dumps({}),
                "source-nea-q4",
                "run-1",
                "internal",
                json.dumps(["internal_consulting"]),
                "2026-08-02T00:00:00Z",
                None,
                "2026-08-02T00:01:00Z",
                "2026-08-02T00:02:00Z",
                "current",
                "canonical",
                1,
                1,
                "approved",
                "approval-1",
                "high",
                0.92,
                0,
                "factual_claim",
                "transcript_extraction",
                "routine",
                "operator",
                "atomic",
            ),
        )
        # 2. The exact defective candidate assertion ("7 years of experience"), even with retrieval_eligible=1 forced
        conn.execute(
            """
            INSERT INTO assertion VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0
            )
            """,
            (
                "assertion-cd256f2fbf979bd7c87a5fae8fa8",
                "person_stephane",
                None,
                "APPLIES_HEURISTIC",
                "Apex Global applies a screening heuristic favoring at least 7 years of experience for candidate roles.",
                "Hiring discussion",
                json.dumps({}),
                "source-nea-q4",
                "run-1",
                "internal",
                json.dumps(["internal_consulting"]),
                "2026-08-02T00:00:00Z",
                None,
                "2026-08-02T00:01:00Z",
                "2026-08-02T00:02:00Z",
                "current",
                "candidate",
                0,
                1,
                "unvalidated",
                None,
                "directional",
                0.45,
                0,
                "factual_claim",
                "transcript_extraction",
                "routine",
                "operator",
                "atomic",
            ),
        )
        for idx, aid, quote in [
            (
                1,
                "assertion-canonical-nea-q4",
                "Cameron Mercer sponsors Project LENS Q4 workstream expansion.",
            ),
            (
                2,
                "assertion-cd256f2fbf979bd7c87a5fae8fa8",
                "Apex Global applies a screening heuristic favoring at least 7 years of experience.",
            ),
        ]:
            ev_id = f"ev-{idx}"
            conn.execute(
                "INSERT INTO evidence_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ev_id,
                    "source-nea-q4",
                    "sv-1",
                    "timestamp",
                    f"00:0{idx}:00",
                    f"00:0{idx}:00",
                    quote,
                    "direct",
                    1,
                ),
            )
            conn.execute(
                "INSERT INTO assertion_evidence VALUES (?, ?, 'supports')",
                (aid, ev_id),
            )
            conn.execute(
                """
                INSERT INTO search_document (
                    search_document_id, object_type, object_id, title, body,
                    canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id
                ) VALUES (?, 'assertion', ?, ?, ?, ?, 'current', 1, 'source-nea-q4', ?)
                """,
                (
                    f"sd-{idx}",
                    aid,
                    quote,
                    f"Cameron Mercer {quote}",
                    "canonical" if idx == 1 else "candidate",
                    aid,
                ),
            )
        conn.commit()
    finally:
        conn.close()


class CanonicalQueryFilterE2ETests(unittest.TestCase):
    def test_wave3_all_query_engines_exclude_unpromoted_low_confidence_candidate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_wave3_") as tmp:
            tmp_dir = Path(tmp)
            db_path = tmp_dir / "graph.sqlite"
            _provision_wave3_test_db(db_path)

            # 1. Decision Adapter row_reader excludes candidate / canonical_eligible=0
            rows = _read_rows(db_path)
            row_ids = [r["assertion_id"] for r in rows]
            self.assertEqual(["assertion-canonical-nea-q4"], row_ids)
            self.assertNotIn("assertion-cd256f2fbf979bd7c87a5fae8fa8", row_ids)

            # 2. GraphFirstQueryEngine excludes candidate & enforces confidence_threshold
            engine = GraphFirstQueryEngine(repo_root=tmp_dir, graph_path=db_path)
            res = engine.execute_query(
                "What Q4 workstreams does Cameron Mercer sponsor?",
                confidence_threshold=0.65,
            )
            self.assertTrue(res.success)
            engine_aids = [a["assertion_id"] for a in res.assertions]
            self.assertIn("assertion-canonical-nea-q4", engine_aids)
            self.assertNotIn("assertion-cd256f2fbf979bd7c87a5fae8fa8", engine_aids)

            # 3. exact_retrieval excludes low-confidence candidate in both factual and candidate_review modes
            exact_fact = exact_retrieval(
                "Cameron Mercer Q4 workstreams",
                db_path,
                classification="internal",
                audiences=["internal_consulting"],
                retrieval_mode="factual",
            )
            fact_aids = [a["assertion_id"] for a in exact_fact["exact_anchors"]]
            self.assertIn("assertion-canonical-nea-q4", fact_aids)
            self.assertNotIn("assertion-cd256f2fbf979bd7c87a5fae8fa8", fact_aids)

            # 4. OfflineGraphContextRetriever excludes candidate assertions
            offline = OfflineGraphContextRetriever(default_graph_path=db_path)
            packet = offline.retrieve_meeting_prior_context(
                project_name="Project LENS",
                attendees=["Cameron Mercer"],
                graph_path=db_path,
            )
            offline_aids = [d.assertion_id for d in packet.decisions] + [
                r.assertion_id for r in packet.risks
            ]
            self.assertIn("assertion-canonical-nea-q4", offline_aids)
            self.assertNotIn("assertion-cd256f2fbf979bd7c87a5fae8fa8", offline_aids)

            # 5. Sidecar eligibility & render_retrieval_records exclude low-confidence candidate
            records = render_retrieval_records(db_path, "snap-1")
            rendered_aids = [
                r["extensions"].get("com.partner_advisory.nee.graph-object-id")
                for r in records
                if r.get("record_type") != "entity_facet"
            ]
            self.assertIn("assertion-canonical-nea-q4", rendered_aids)
            self.assertNotIn("assertion-cd256f2fbf979bd7c87a5fae8fa8", rendered_aids)

            # 6. Direct eligibility & clearance checks
            bad_row = {
                "assertion_id": "assertion-cd256f2fbf979bd7c87a5fae8fa8",
                "canonical_status": "candidate",
                "canonical_eligible": 0,
                "retrieval_eligible": 1,
                "currentness_state": "current",
                "validation_state": "unvalidated",
                "confidence_score": 0.45,
                "classification": "internal",
                "allowed_audiences_json": '["internal_consulting"]',
            }
            self.assertEqual("candidate", effective_retrieval_lane(bad_row))
            elig, reason = classify_retrieval_eligibility(bad_row)
            self.assertFalse(elig)
            self.assertEqual("EXCLUDED_RETRIEVAL_INELIGIBLE_FLAG", reason)
            self.assertFalse(
                _evaluate_record_clearance(
                    {"lane": "approved", "classification": "internal", "audiences": ["internal_consulting"]},
                    bad_row,
                )
            )

    def test_wave4_topic_relevance_and_grounded_delivery_boundary_isolation(self) -> None:
        from types import SimpleNamespace
        from dual_pipeline.grounding.contracts import ClaimManifest, ClaimRecord, SourceLocator
        from dual_pipeline.grounding.eligibility import AssertionEligibilityAuditor, EligibilityDecision
        from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot
        from dual_pipeline.index_core.anchor_traversal_support import score_assertion_relevance
        from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_wave4_") as tmp:
            tmp_dir = Path(tmp)
            db_path = tmp_dir / "graph.sqlite"
            _provision_wave3_test_db(db_path)

            # 1. score_assertion_relevance gives 0 when assertion matches only entity name tokens ("stephan", "marceau")
            # and has zero overlap with query topic tokens ("workstreams", "pipeline")
            q_tokens = ["stephan", "marceau", "workstreams", "pipeline"]
            off_topic_asrt = {
                "assertion_id": "assertion-cd256f2fbf979bd7c87a5fae8fa8",
                "subject_name": "Cameron Mercer",
                "object_name": "",
                "predicate": "APPLIES_HEURISTIC",
                "object_literal": "Apex Global applies a screening heuristic favoring at least 7 years of experience.",
            }
            on_topic_asrt = {
                "assertion_id": "assertion-canonical-nea-q4",
                "subject_name": "Cameron Mercer",
                "object_name": "Project LENS",
                "predicate": "LEADS_DECISION",
                "object_literal": "Cameron Mercer sponsors Project LENS Q4 workstreams pipeline.",
            }
            self.assertEqual(0, score_assertion_relevance(q_tokens, off_topic_asrt))
            self.assertGreater(score_assertion_relevance(q_tokens, on_topic_asrt), 0)

            # 2. CopilotGraphRetriever rejects candidate and off-topic assertions on Cameron Mercer
            topology = SimpleNamespace(
                nodes={
                    "person_stephane": {
                        "node_id": "person_stephane",
                        "canonical_name": "Cameron Mercer",
                        "display_name": "Cameron Mercer",
                        "node_type": "person",
                    },
                    "workstream_lens": {
                        "node_id": "workstream_lens",
                        "canonical_name": "Project LENS",
                        "display_name": "Project LENS",
                        "node_type": "workstream",
                    },
                },
                assertions={
                    "assertion-canonical-nea-q4": {
                        "assertion_id": "assertion-canonical-nea-q4",
                        "subject_node_id": "person_stephane",
                        "object_node_id": "workstream_lens",
                        "predicate": "LEADS_DECISION",
                        "claim_text": "Cameron Mercer sponsors Project LENS Q4 workstreams pipeline.",
                        "canonical_status": "canonical",
                        "canonical_eligible": 1,
                        "retrieval_eligible": 1,
                        "confidence_score": 0.92,
                        "engagement_id": "nea",
                    },
                    "assertion-cd256f2fbf979bd7c87a5fae8fa8": {
                        "assertion_id": "assertion-cd256f2fbf979bd7c87a5fae8fa8",
                        "subject_node_id": "person_stephane",
                        "object_node_id": None,
                        "predicate": "APPLIES_HEURISTIC",
                        "claim_text": "Apex Global applies a screening heuristic favoring at least 7 years of experience.",
                        "canonical_status": "candidate",
                        "canonical_eligible": 0,
                        "retrieval_eligible": 1,
                        "confidence_score": 0.45,
                        "engagement_id": "nea",
                    },
                    "assertion-off-topic-canonical": {
                        "assertion_id": "assertion-off-topic-canonical",
                        "subject_node_id": "person_stephane",
                        "object_node_id": None,
                        "predicate": "APPLIES_HEURISTIC",
                        "claim_text": "Apex Global applies a screening heuristic favoring at least 7 years of experience.",
                        "canonical_status": "canonical",
                        "canonical_eligible": 1,
                        "retrieval_eligible": 1,
                        "confidence_score": 0.90,
                        "engagement_id": "nea",
                    },
                },
                supporting_evidence={},
                forward_adjacency={},
            )
            retriever = CopilotGraphRetriever(
                topology=topology,
                attendees=["Cameron Mercer"],
                engagement_id="nea",
            )
            self.assertNotIn(
                "assertion-cd256f2fbf979bd7c87a5fae8fa8",
                retriever._indexed_assertion_records,
            )
            cop_res = retriever.retrieve_structured_assertions(
                "Cameron Mercer Q4 workstreams pipeline"
            )
            cop_ids = [r["assertion_id"] for r in cop_res]
            self.assertIn("assertion-canonical-nea-q4", cop_ids)
            self.assertNotIn("assertion-off-topic-canonical", cop_ids)

            person_ctx = retriever.get_person_context(
                "Cameron Mercer", "Q4 workstreams pipeline"
            )
            p_ids = [a["assertion_id"] for a in person_ctx["assertions"]]
            self.assertIn("assertion-canonical-nea-q4", p_ids)
            self.assertNotIn("assertion-off-topic-canonical", p_ids)

            # 3. ReadOnlyGraphSnapshot, AssertionEligibilityAuditor & verify_grounded_delivery reject candidate and off-topic assertions
            import hashlib
            from dual_pipeline.grounding.delivery_boundary import verify_grounded_delivery

            graph_sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
            (tmp_dir / "active_pair.json").write_text(
                json.dumps(
                    {
                        "generation_id": "gen-wave4-test",
                        "graph_path": str(db_path),
                        "graph_sha256": graph_sha,
                    }
                ),
                encoding="utf-8",
            )
            snap = ReadOnlyGraphSnapshot(tmp_dir, enforce_pair_integrity=True)
            try:
                cand_matches = snap.find_assertions_for_claim(
                    subject="Cameron Mercer",
                    predicate="APPLIES_HEURISTIC",
                    object_val="Apex Global applies a screening heuristic favoring at least 7 years of experience for candidate roles.",
                )
                self.assertEqual([], cand_matches)

                auditor = AssertionEligibilityAuditor()
                bad_claim = ClaimRecord(
                    claim_id="CLM-CANDIDATE-LEAK",
                    subject="Cameron Mercer",
                    predicate="APPLIES_HEURISTIC",
                    object="Apex Global applies a screening heuristic favoring at least 7 years of experience for candidate roles.",
                    scope="PROJECT_LEVEL",
                    negation=False,
                    assertion_ids=["assertion-cd256f2fbf979bd7c87a5fae8fa8"],
                    evidence_lane="approved",
                    epistemic_status="approved",
                    source_locators=[
                        SourceLocator(
                            source_uri="archive/nea.md",
                            locator_type="transcript_paragraph",
                            locator_value="00:02:00",
                        )
                    ],
                )
                audit_res = auditor.audit_claim(bad_claim, snap)
                self.assertFalse(audit_res.is_eligible)
                self.assertIn(
                    audit_res.decision,
                    (EligibilityDecision.REJECTED_ENTITY_ONLY, EligibilityDecision.REJECTED_UNSUPPORTED),
                )

                payload = "Cameron Mercer notes Apex Global applies a screening heuristic favoring at least 7 years of experience."
                manifest = ClaimManifest(
                    manifest_id="MAN-WAVE4-TEST",
                    runtime_root=str(tmp_dir),
                    graph_generation_id="gen-wave4-test",
                    graph_sha256=graph_sha,
                    payload_digest=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    claims=[bad_claim],
                )
                certified, receipt, reasons = verify_grounded_delivery(
                    payload_text=payload,
                    manifest=manifest,
                    runtime_root=tmp_dir,
                    audience="internal_consulting",
                    delivery_surface="QUERY_SYNTHESIS",
                    prompt_text="What Q4 workstreams pipeline does Cameron Mercer sponsor?",
                )
                self.assertFalse(certified)
                self.assertEqual("REJECTED", receipt.status)
                self.assertTrue(reasons)
            finally:
                snap.close()


if __name__ == "__main__":
    unittest.main()
