"""Unit & integration tests for Wave 2: Pre-Publish C0 Dry-Run & Exception-Only HITL Delta Card."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.core import sha256_file
from dual_pipeline.intake_core.dispositions import build_dispositions
from dual_pipeline.intake_core.hitl import (
    build_shift_left_exception_digest,
    create_shift_left_hitl_decision,
)
from dual_pipeline.intake_engine import IntakeEngine
from dual_pipeline.hitl_governance_core.persistence import persist_shift_left_hitl_decisions


class Wave2PrePublishC0HitlDigestTests(unittest.TestCase):
    """Verify C0 preview dry-run, exception-only filtering, atomic decision resolution, and alias persistence."""

    def _create_bundle(
        self,
        candidates: list[dict[str, Any]],
        *,
        inline_promo: bool = True,
        authority_rank: int = 7,
    ) -> dict[str, Any]:
        return {
            "run_id": "run-" + "b" * 28,
            "trace_id": "1" * 32,
            "source_id": "source-test-w2",
            "candidates": candidates,
            "extensions": {
                "com.partner_advisory.nee.context": {
                    "authority_rank": authority_rank,
                    "inline_canonical_promotion": inline_promo,
                    "source_kind": "evidence_envelope",
                },
                "com.partner_advisory.nee.inline-canonical-promotion": inline_promo,
            },
        }

    def test_conflict_free_package_has_zero_exceptions(self) -> None:
        candidates = [
            {
                "candidate_id": f"cand-clean-{i:02d}",
                "candidate_type": "assertion" if i % 2 == 0 else "action",
                "assertion_type": "candidate_assertion" if i % 2 == 0 else "action_item",
                "extraction_confidence": 0.75 + (i * 0.01),
                "evidence_ids": [f"ev-{i}"],
                "entity_mentions": [],
                "classification": "confidential",
                "audiences": ["internal_consulting"],
                "materiality": "MEDIUM",
            }
            for i in range(12)
        ]
        bundle = self._create_bundle(candidates)
        routes = [
            {
                "subject_id": c["candidate_id"],
                "intake_route": "NEW_ASSERTION" if c["candidate_type"] == "assertion" else "ACTION_ITEM",
                "reason_codes": [],
            }
            for c in candidates
        ]
        disps = build_dispositions(bundle, routes, [])
        digest = build_shift_left_exception_digest(bundle, routes, [], disps)

        self.assertFalse(digest["has_exceptions"])
        self.assertEqual(digest["exception_count"], 0)
        self.assertEqual(digest["tier_0_auto_promoted_count"], 12)
        self.assertEqual(len(digest["exceptions"]), 0)

    def test_contradiction_exception_and_supersede_resolution(self) -> None:
        cand_clean = {
            "candidate_id": "cand-clean-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.85,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        cand_contra = {
            "candidate_id": "cand-contra-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.90,
            "evidence_ids": ["ev-2"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "HIGH",
        }
        bundle = self._create_bundle([cand_clean, cand_contra])
        routes = [
            {"subject_id": "cand-clean-01", "intake_route": "NEW_ASSERTION", "reason_codes": []},
            {"subject_id": "cand-contra-01", "intake_route": "CONTRADICTION", "reason_codes": []},
        ]
        comparison = {
            "comparisons": {
                "cand-contra-01": {
                    "contradiction_detected": True,
                    "exact_matches": [{"object_id": "assertion-prior-001"}],
                }
            }
        }
        disps_before = build_dispositions(bundle, routes, [])
        digest = build_shift_left_exception_digest(bundle, routes, [], disps_before, comparison)

        self.assertTrue(digest["has_exceptions"])
        self.assertEqual(digest["exception_count"], 1)
        self.assertEqual(digest["tier_0_auto_promoted_count"], 1)

        exc = digest["exceptions"][0]
        self.assertEqual(exc["kind"], "CONTRADICTION")
        self.assertEqual(exc["candidate_id"], "cand-contra-01")
        self.assertEqual(exc["existing_target_id"], "assertion-prior-001")
        opt_decisions = [o["decision"] for o in exc["options"]]
        self.assertIn("SUPERSEDE", opt_decisions)
        self.assertIn("KEEP_CANDIDATE", opt_decisions)
        self.assertIn("QUARANTINE", opt_decisions)

        # 1. Resolve with SUPERSEDE
        decision_art = create_shift_left_hitl_decision(
            bundle, exc, "SUPERSEDE", reviewer="operator", rationale="Verified state supersession"
        )
        validate_artifact(decision_art)
        self.assertEqual(decision_art["decision"], "SUPERSEDE")

        disps_superseded = build_dispositions(bundle, routes, [], decisions=[decision_art])
        d_contra = next(d for d in disps_superseded if d["candidate_id"] == "cand-contra-01")
        self.assertEqual(d_contra["disposition"], "APPROVED")
        self.assertEqual(d_contra["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")

        # 2. Resolve with KEEP_CANDIDATE
        decision_keep = create_shift_left_hitl_decision(
            bundle, exc, "KEEP_CANDIDATE", reviewer="operator", rationale="Retain prior fact"
        )
        disps_keep = build_dispositions(bundle, routes, [], decisions=[decision_keep])
        d_keep = next(d for d in disps_keep if d["candidate_id"] == "cand-contra-01")
        self.assertEqual(d_keep["disposition"], "CANDIDATE")
        self.assertEqual(d_keep["x3_outcome"], "X3D_ALLOW_CANDIDATE_STAGE")

    def test_entity_alias_review_exception_and_merge_alias_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "test_graph.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """CREATE TABLE canonical_alias (
                        canonical_alias_id TEXT PRIMARY KEY,
                        normalized_alias TEXT,
                        display_alias TEXT,
                        entity_id TEXT,
                        alias_type TEXT,
                        source_id TEXT,
                        confidence_label TEXT,
                        confidence_score REAL,
                        ambiguity_state TEXT,
                        preferred_flag INTEGER,
                        review_state TEXT,
                        active INTEGER,
                        created_at TEXT
                    )"""
                )
                conn.commit()

            cand_alias = {
                "candidate_id": "cand-alias-01",
                "candidate_type": "entity_mention",
                "assertion_type": "candidate_assertion",
                "reported_name": "Michael Reiss",
                "extraction_confidence": 0.88,
                "evidence_ids": ["ev-1"],
                "entity_mentions": [{"mention_id": "m-reiss"}],
                "classification": "confidential",
                "audiences": ["internal_consulting"],
                "materiality": "MEDIUM",
            }
            bundle = self._create_bundle([cand_alias])
            routes = [{"subject_id": "cand-alias-01", "intake_route": "ENTITY_ALIAS_REVIEW", "reason_codes": []}]
            resolutions = [
                {
                    "mention_id": "m-reiss",
                    "outcome": "HITL_CONFIRM_ENTITY",
                    "requires_hitl": True,
                    "extensions": {
                        "com.partner_advisory.nee.identity-review": {
                            "label_options": [{"target_id": "person:michael-rice", "decision": "CONFIRM"}]
                        }
                    },
                }
            ]
            disps_before = build_dispositions(bundle, routes, resolutions)
            digest = build_shift_left_exception_digest(bundle, routes, resolutions, disps_before)

            self.assertTrue(digest["has_exceptions"])
            exc = digest["exceptions"][0]
            self.assertEqual(exc["kind"], "ENTITY_ALIAS_REVIEW")
            self.assertEqual(exc["existing_target_id"], "person:michael-rice")

            # Resolve with MERGE_ALIAS
            decision_art = create_shift_left_hitl_decision(
                bundle, exc, "MERGE_ALIAS", reviewer="operator", rationale="Phonetic name variant"
            )
            validate_artifact(decision_art)
            self.assertEqual(decision_art["decision"], "CONFIRM")
            self.assertEqual(
                decision_art["extensions"]["com.partner_advisory.nee.selected-entity-id"],
                "person:michael-rice",
            )

            disps_after = build_dispositions(bundle, routes, resolutions, decisions=[decision_art])
            self.assertEqual(disps_after[0]["disposition"], "APPROVED")

            # Persist decisions and verify canonical_alias entry
            persist_shift_left_hitl_decisions(
                decisions=[decision_art],
                output_dir=temp_path,
                runtime_root=temp_path,
                graph_path=db_path,
            )

            # Check ledger
            ledger_file = temp_path / "hitl_decision_ledger.json"
            self.assertTrue(ledger_file.is_file())
            ledger_data = json.loads(ledger_file.read_text(encoding="utf-8"))
            self.assertEqual(len(ledger_data), 1)

            # Check canonical_alias table
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT display_alias, entity_id, active FROM canonical_alias WHERE entity_id='person:michael-rice'"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "Michael Reiss")
                self.assertEqual(row[1], "person:michael-rice")
                self.assertEqual(row[2], 1)

    def test_supersession_state_flip_resolution(self) -> None:
        cand_super = {
            "candidate_id": "cand-super-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.88,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "HIGH",
        }
        bundle = self._create_bundle([cand_super])
        routes = [{"subject_id": "cand-super-01", "intake_route": "SUPERSESSION", "reason_codes": []}]
        disps_before = build_dispositions(bundle, routes, [])
        digest = build_shift_left_exception_digest(bundle, routes, [], disps_before)

        self.assertTrue(digest["has_exceptions"])
        exc = digest["exceptions"][0]
        self.assertEqual(exc["kind"], "SUPERSESSION")

        decision_art = create_shift_left_hitl_decision(
            bundle, exc, "SUPERSEDE", reviewer="operator", rationale="Lead architect rotated"
        )
        disps_after = build_dispositions(bundle, routes, [], decisions=[decision_art])
        self.assertEqual(disps_after[0]["disposition"], "APPROVED")

    def test_intake_engine_preview_c0_exceptions_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "graph.sqlite"
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
                        extraction_confidence REAL
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

            # Create package
            pkg_path = temp_path / "package.json"
            package = {
                "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
                "schema_version": "1.0.0",
                "artifact_id": "artifact-preview-test",
                "meeting": {
                    "meeting_id": "meeting-preview-01",
                    "title": "Preview Test Meeting",
                    "started_at": "2026-10-30T10:00:00Z",
                    "source_location": "archive://notes/meeting.md",
                    "source_hash": "a" * 64,
                    "attendees": ["Alex Brannan"],
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
                                "text": "Platform team confirms readiness for preview dry-run.",
                                "locator": {"kind": "timestamp", "value": "01:15"},
                                "gemini_confidence": 0.85,
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
            pkg_path.write_text(json.dumps(seal_artifact(package), indent=2), encoding="utf-8")

            hash_before = sha256_file(db_path)
            engine = IntakeEngine(graph_path=db_path, runtime_root=temp_path)
            digest = engine.preview_c0_exceptions(pkg_path)
            hash_after = sha256_file(db_path)

            # Ensure zero mutation during preview dry run
            self.assertEqual(hash_before, hash_after)
            self.assertEqual(digest["tier_0_auto_promoted_count"], 1)
            self.assertFalse(digest["has_exceptions"])
            self.assertEqual(digest["exception_count"], 0)


if __name__ == "__main__":
    unittest.main()
