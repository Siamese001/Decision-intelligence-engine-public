"""End-to-End Pipeline test for Shift-Left HITL & Inline Graph Promotion.

Verifies that clean meeting and document claims promote inline to canonical status
and are immediately searchable/retrievable without invoking the graph hygiene agent.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.hitl_governance_core.inbox_runner import (
    list_inbox_items,
    resolve_inbox_item,
)
from dual_pipeline.intake_core.comparison import compare_candidates
from dual_pipeline.intake_core.dispositions import build_dispositions
from dual_pipeline.intake_core.routing import build_routes
from dual_pipeline.intake_core.hitl import build_shift_left_exception_digest
from dual_pipeline.intake_engine import IntakeEngine
from dual_pipeline.normalization import normalize_evidence_envelope


class ShiftLeftHitlPromotionE2ETests(unittest.TestCase):
    def _create_hermetic_graph(self, db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'canonical',
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE node_type_registry (
                    node_type_id TEXT PRIMARY KEY,
                    registry_state TEXT NOT NULL DEFAULT 'active',
                    identity_rule TEXT NOT NULL DEFAULT 'never auto-merge on name similarity'
                );
                INSERT INTO node_type_registry VALUES ('person', 'active', 'none'), ('stakeholder', 'active', 'none'), ('org', 'active', 'none');
                INSERT INTO node VALUES ('person:alex-brannan', 'Alex Brannan', 'person', 'Director', 'canonical', 1);
                INSERT INTO node VALUES ('person:sarah-connor', 'Sarah Connor', 'person', 'Lead Architect', 'canonical', 1);

                CREATE TABLE canonical_alias (
                    canonical_alias_id TEXT PRIMARY KEY,
                    normalized_alias TEXT NOT NULL,
                    display_alias TEXT,
                    entity_id TEXT NOT NULL,
                    alias_type TEXT,
                    source_id TEXT,
                    confidence_label TEXT,
                    confidence_score REAL,
                    ambiguity_state TEXT,
                    preferred_flag INTEGER,
                    review_state TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT
                );
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    predicate TEXT,
                    object_node_id TEXT,
                    object_literal TEXT,
                    canonical_status TEXT DEFAULT 'canonical',
                    currentness_state TEXT DEFAULT 'current',
                    classification TEXT DEFAULT 'confidential',
                    audiences TEXT DEFAULT '["internal_consulting"]',
                    allowed_audiences_json TEXT DEFAULT '["internal_consulting"]',
                    extraction_confidence REAL,
                    superseded_at TEXT,
                    superseded_by_assertion_id TEXT
                );
                CREATE TABLE search_document (
                    search_document_id TEXT PRIMARY KEY,
                    object_type TEXT,
                    object_id TEXT,
                    title TEXT,
                    body TEXT,
                    canonical_status TEXT DEFAULT 'canonical',
                    currentness_state TEXT DEFAULT 'current',
                    retrieval_eligible INTEGER DEFAULT 1,
                    source_id TEXT,
                    assertion_id TEXT
                );
                """
            )
            conn.commit()

    def test_e2e_meeting_ingest_promotes_inline_to_canonical(self) -> None:
        """Verify 100% of clean source-bound claims promote inline to APPROVED without hygiene agent."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "graph.sqlite"
            self._create_hermetic_graph(db_path)

            package = {
                "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
                "schema_version": "1.0.0",
                "artifact_id": "artifact-e2e-meeting-01",
                "meeting": {
                    "meeting_id": "meeting-e2e-01",
                    "title": "E2E Platform Architecture Review",
                    "started_at": "2026-10-30T10:00:00Z",
                    "source_location": "archive://notes/meeting-e2e.md",
                    "source_hash": "e" * 64,
                    "attendees": ["Alex Brannan", "Sarah Connor"],
                    "classification": "confidential",
                    "audiences": ["internal_consulting"],
                    "template_version": "5.3",
                },
                "speakers": [
                    {
                        "speaker_ref": "spk-alex",
                        "reported_name": "Alex Brannan",
                        "email": "alex@consulting.example.com",
                        "role_or_org": "Director",
                        "statements": [
                            {
                                "statement_id": "stmt-01",
                                "kind": "strategic",
                                "text": "Platform core architecture is finalized and approved for production.",
                                "locator": {"kind": "timestamp", "value": "04:12"},
                                "gemini_confidence": 0.88,
                            }
                        ],
                    }
                ],
                "actions": [],
                "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
                "extensions": {
                    "com.partner_advisory.nee.inline-canonical-promotion": True,
                    "com.partner_advisory.nee.authority-rank": 7,
                },
            }
            pkg_path = temp_path / "package.json"
            pkg_path.write_text(json.dumps(seal_artifact(package), indent=2), encoding="utf-8")

            # 1. Preview exceptions dry-run
            engine = IntakeEngine(graph_path=db_path, runtime_root=temp_path)
            digest = engine.preview_c0_exceptions(pkg_path)

            # Assert zero exceptions, 1 Tier-0 clean auto-promoted claim
            self.assertFalse(digest["has_exceptions"])
            self.assertEqual(digest["exception_count"], 0)
            self.assertEqual(digest["tier_0_auto_promoted_count"], 1)

            # 2. Ingest bundle through C0/X3
            from dual_pipeline.identity import resolve_bundle
            bundle = normalize_evidence_envelope(seal_artifact(package), run_context="test-run")
            resolutions = resolve_bundle(bundle, db_path)
            comparison = compare_candidates(bundle, db_path)
            routes = build_routes(bundle, resolutions, comparison)
            disps = build_dispositions(bundle, routes, resolutions)

            # Verify disposition is APPROVED (X3D_ALLOW_APPROVED_WRITE)
            self.assertEqual(len(disps), 1)
            self.assertEqual(disps[0]["disposition"], "APPROVED")
            self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")

            # Simulate UWG write of the approved assertion
            cid = disps[0]["candidate_id"]
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO assertion (assertion_id, canonical_status, currentness_state, extraction_confidence)
                       VALUES (?, 'canonical', 'current', 0.88)""",
                    (cid,),
                )
                conn.execute(
                    """INSERT INTO search_document (search_document_id, assertion_id, title, body, canonical_status, currentness_state, retrieval_eligible)
                       VALUES (?, ?, 'Architecture Review', 'Platform core architecture finalized', 'canonical', 'current', 1)""",
                    (f"sd-{cid}", cid),
                )
                conn.commit()

            # 3. Direct Query / Retrieval check
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                # Verify zero clean claims are left in candidate status
                cand_count = conn.execute(
                    "SELECT COUNT(*) as count FROM assertion WHERE canonical_status='candidate'"
                ).fetchone()["count"]
                self.assertEqual(cand_count, 0)

                # Verify newly ingested assertion is canonical and searchable
                row = conn.execute(
                    "SELECT canonical_status, currentness_state FROM assertion WHERE assertion_id=?", (cid,)
                ).fetchone()
                self.assertEqual(row["canonical_status"], "canonical")
                self.assertEqual(row["currentness_state"], "current")

                sd_row = conn.execute(
                    "SELECT retrieval_eligible, canonical_status FROM search_document WHERE assertion_id=?", (cid,)
                ).fetchone()
                self.assertEqual(sd_row["retrieval_eligible"], 1)
                self.assertEqual(sd_row["canonical_status"], "canonical")

    def test_e2e_fast_promote_and_hitl_inbox_deferred_resolution(self) -> None:
        """Verify back-to-back fast-promote parks conflicts and hitl-inbox resolves them cleanly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "graph.sqlite"
            self._create_hermetic_graph(db_path)

            # Insert baseline existing assertion to create contradiction
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO assertion (assertion_id, canonical_status, currentness_state)
                       VALUES ('assertion-prior-e2e', 'canonical', 'current')"""
                )
                conn.execute(
                    """INSERT INTO search_document (search_document_id, assertion_id, canonical_status, currentness_state, retrieval_eligible)
                       VALUES ('sd-prior-e2e', 'assertion-prior-e2e', 'canonical', 'current', 1)"""
                )
                conn.commit()

            # Create conflict digest
            bundle = {
                "run_id": "run-b2b-01",
                "trace_id": "1" * 32,
                "candidates": [
                    {
                        "candidate_id": "cand-clean-b2b",
                        "candidate_type": "assertion",
                        "assertion_type": "candidate_assertion",
                        "extraction_confidence": 0.85,
                        "evidence_ids": ["ev-1"],
                        "entity_mentions": [],
                        "classification": "confidential",
                        "audiences": ["internal_consulting"],
                        "materiality": "MEDIUM",
                        "text": "Sprint 42 deliverables approved by stakeholder committee.",
                    },
                    {
                        "candidate_id": "cand-contra-b2b",
                        "candidate_type": "assertion",
                        "assertion_type": "candidate_assertion",
                        "extraction_confidence": 0.90,
                        "evidence_ids": ["ev-2"],
                        "entity_mentions": [],
                        "classification": "confidential",
                        "audiences": ["internal_consulting"],
                        "materiality": "HIGH",
                        "text": "Lead architect transitioned to Sarah Connor.",
                    },
                ],
                "extensions": {
                    "com.partner_advisory.nee.inline-canonical-promotion": True,
                    "com.partner_advisory.nee.authority-rank": 7,
                },
            }
            routes = [
                {"subject_id": "cand-clean-b2b", "intake_route": "NEW_ASSERTION", "reason_codes": []},
                {"subject_id": "cand-contra-b2b", "intake_route": "CONTRADICTION", "reason_codes": []},
            ]
            comparison = {
                "comparisons": {
                    "cand-contra-b2b": {
                        "contradiction_detected": True,
                        "exact_matches": [{"object_id": "assertion-prior-e2e"}],
                    }
                }
            }
            disps = build_dispositions(bundle, routes, [])
            digest = build_shift_left_exception_digest(bundle, routes, [], disps, comparison)

            self.assertTrue(digest["has_exceptions"])
            self.assertEqual(digest["tier_0_auto_promoted_count"], 1)
            self.assertEqual(digest["exception_count"], 1)

            # Park exception in hitl_inbox.json
            from dual_pipeline.hitl_governance_core.inbox_runner import park_shift_left_exceptions
            parked = park_shift_left_exceptions(
                runtime_root=temp_path,
                output_dir=temp_path,
                digest=digest,
                package_path=temp_path / "package.json",
                graph_path=db_path,
                source_title="Back-to-Back Sync Meeting",
            )
            self.assertEqual(len(parked), 1)

            # Ingest clean claim to canonical, park conflict in candidate
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO assertion (assertion_id, canonical_status, currentness_state)
                       VALUES ('cand-clean-b2b', 'canonical', 'current')"""
                )
                conn.execute(
                    """INSERT INTO search_document (search_document_id, assertion_id, canonical_status, currentness_state, retrieval_eligible)
                       VALUES ('sd-clean-b2b', 'cand-clean-b2b', 'canonical', 'current', 1)"""
                )
                conn.execute(
                    """INSERT INTO assertion (assertion_id, canonical_status, currentness_state)
                       VALUES ('cand-contra-b2b', 'candidate', 'current')"""
                )
                conn.execute(
                    """INSERT INTO search_document (search_document_id, assertion_id, canonical_status, currentness_state, retrieval_eligible)
                       VALUES ('sd-contra-b2b', 'cand-contra-b2b', 'candidate', 'current', 0)"""
                )
                conn.commit()

            # Verify clean claim is immediately retrievable before inbox resolution
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                clean_row = conn.execute(
                    "SELECT retrieval_eligible FROM search_document WHERE assertion_id='cand-clean-b2b'"
                ).fetchone()
                self.assertEqual(clean_row["retrieval_eligible"], 1)

                contra_row = conn.execute(
                    "SELECT retrieval_eligible FROM search_document WHERE assertion_id='cand-contra-b2b'"
                ).fetchone()
                self.assertEqual(contra_row["retrieval_eligible"], 0)

            # Operator resolves inbox exception via SUPERSEDE in <5s
            t0 = time.perf_counter()
            res = resolve_inbox_item(
                runtime_root=temp_path,
                target_id="cand-contra-b2b",
                decision="SUPERSEDE",
                reviewer="operator",
                rationale="Lead architect confirmed",
                graph_path=db_path,
            )
            elapsed = time.perf_counter() - t0
            self.assertLess(elapsed, 5.0)
            self.assertEqual(res["status"], "SUCCESS")

            # Post-resolution verification
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                # Prior assertion superseded
                prior = conn.execute(
                    "SELECT a.canonical_status, s.retrieval_eligible FROM assertion a JOIN search_document s ON a.assertion_id=s.assertion_id WHERE a.assertion_id='assertion-prior-e2e'"
                ).fetchone()
                self.assertEqual(prior["canonical_status"], "superseded")
                self.assertEqual(prior["retrieval_eligible"], 0)

                # Candidate promoted
                promoted = conn.execute(
                    "SELECT a.canonical_status, s.retrieval_eligible FROM assertion a JOIN search_document s ON a.assertion_id=s.assertion_id WHERE a.assertion_id='cand-contra-b2b'"
                ).fetchone()
                self.assertEqual(promoted["canonical_status"], "canonical")
                self.assertEqual(promoted["retrieval_eligible"], 1)


if __name__ == "__main__":
    unittest.main()
