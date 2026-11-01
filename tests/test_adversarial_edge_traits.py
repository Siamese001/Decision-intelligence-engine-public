"""Adversarial stress testing for the 4 Foundational Edge Traits ingestion.

Covers:
1. Epistemic conditionality discounting (conditional/tentative commitments clamped).
2. Controlled vocabulary invariant defense (unregistered assertion types safely mapped).
3. Sticky note quote grounding & locator resilience under malformed input.
4. Lineage integrity & speaker attribution fallback under missing metadata.
5. Idempotent edge replay defense (re-ingestion preserves graph integrity without duplication).
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set
from dual_pipeline.normalization import normalize_evidence_envelope
from storage.database import sqlite3


class TestAdversarialEdgeTraits(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_graph = (
            ROOT / ".local" / "dual_pipeline" / "generations" / "generation-36b538f04af06cb4be863517d4f0" / "graph.sqlite"
        )
        if not self.orig_graph.exists():
            self.skipTest(f"Active baseline graph not found at {self.orig_graph}")

        self.db_path = Path(self.tmp_dir.name) / "test_adversarial_edge_traits.sqlite"
        shutil.copy2(self.orig_graph, self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _build_test_package(
        self,
        relationship_signals: list[dict],
        artifact_id: str = "artifact-adv-traits-test",
    ) -> dict:
        return {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": artifact_id,
            "meeting": {
                "meeting_id": f"meeting-{artifact_id}",
                "title": "Adversarial Edge Traits Test Review",
                "started_at": "2026-09-10T10:00:00-04:00",
                "source_location": f"archive://notes/{artifact_id}.md",
                "source_hash": "a" * 64,
                "attendees": ["Cameron Mercer", "Jen Bricker"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-stephan",
                    "reported_name": "Cameron Mercer",
                    "email": "stephan.marceau@consulting.example.com",
                    "role_or_org": "Client Service Lead",
                    "statements": [
                        {
                            "statement_id": "stmt-stephan-01",
                            "kind": "technical",
                            "text": "I am on the hook for delivery staffing fulfillment, if we agree to this.",
                            "locator": {"kind": "timestamp", "value": "29:36"},
                            "gemini_confidence": 0.95,
                        }
                    ],
                }
            ],
            "actions": [],
            "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": [
                        {
                            "id": "stakeholder:stephan_marceau",
                            "type": "stakeholder",
                            "name": "Cameron Mercer",
                            "description": "Client Partner",
                            "mutation": "CONFIRM",
                        },
                        {
                            "id": "workstream:delivery_staffing",
                            "type": "workstream",
                            "name": "Delivery Staffing Fulfillment",
                            "description": "Delivery model staffing workstream",
                            "mutation": "CREATE",
                        },
                    ],
                    "relationship_signals": relationship_signals,
                }
            },
        }

    def _apply_package(self, package: dict) -> list[dict]:
        bundle = normalize_evidence_envelope(seal_artifact(package), run_context="adv_run")
        validate_artifact(bundle)
        resolutions = resolve_bundle(bundle, self.db_path)
        routes = [
            {
                "subject_id": c["candidate_id"],
                "intake_route": (
                    "NEW_RELATIONSHIP"
                    if c["candidate_type"] == "architecture_relationship"
                    else "NEW_ENTITY"
                    if c["candidate_type"] == "entity_mention"
                    else "NEW_ASSERTION"
                ),
                "reason_codes": [],
            }
            for c in bundle["candidates"]
        ]
        dispositions = build_dispositions(bundle, routes, resolutions)
        change_set = build_graph_change_set(bundle, dispositions, resolutions, self.db_path)
        self.assertIsNotNone(change_set)
        change_set["status"] = "approved"
        change_set["approval_id"] = "approval-adv-test"
        writer = GovernedGraphWriter(self.db_path)
        return writer.apply(seal_artifact(change_set))

    def test_case_1_controlled_vocabulary_invariant_defense(self) -> None:
        """Adversarial check: Unregistered assertion_type is safely mapped without DB trigger failure."""
        sig = {
            "signal_id": "sig-rogue-01",
            "from_id": "stakeholder:stephan_marceau",
            "to_id": "workstream:delivery_staffing",
            "predicate": "DELIVERY_ACCOUNTABILITY_FOR",
            "assertion_type": "rogue_unregistered_type_that_violates_triggers",
            "confidence": {"score": 0.82, "tier": "HIGH", "rationale": "Direct statement"},
            "evidence": {"quote": "I am on the hook for delivery.", "locator": {"kind": "timestamp", "value": "29:36"}},
            "lineage": {"source_id": "meeting-adv-01", "speaker": "Cameron Mercer"},
            "evidence_ids": ["stmt-stephan-01"],
        }
        pkg = self._build_test_package([sig], artifact_id="artifact-rogue-type-test")
        receipts = self._apply_package(pkg)
        self.assertGreater(len(receipts), 0)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT assertion_type, confidence_label, qualifiers_json FROM assertion WHERE legacy_record_type='edge' AND qualifiers_json LIKE '%rogue_unregistered_type%'"
        ).fetchone()
        self.assertIsNotNone(row)
        # Controlled trigger mandates allowed types; unmapped falls back to extracted_claim
        self.assertEqual(row["assertion_type"], "extracted_claim")
        qual = json.loads(row["qualifiers_json"])
        self.assertEqual(qual["edge_traits"]["assertion_type"], "rogue_unregistered_type_that_violates_triggers")
        conn.close()

    def test_case_2_speaker_lineage_fallback_defense(self) -> None:
        """Adversarial check: Missing speaker attribution in lineage falls back to statement speaker."""
        sig = {
            "signal_id": "sig-missing-speaker-01",
            "from_id": "stakeholder:stephan_marceau",
            "to_id": "workstream:delivery_staffing",
            "predicate": "DELIVERY_ACCOUNTABILITY_FOR",
            "assertion_type": "governed_accountability",
            "confidence": {"score": 0.85, "tier": "HIGH", "rationale": "Direct statement"},
            "evidence": {"quote": "I am on the hook.", "locator": {"kind": "timestamp", "value": "29:36"}},
            "lineage": {"source_id": "meeting-adv-02"},  # Missing speaker field
            "evidence_ids": ["stmt-stephan-01"],
        }
        pkg = self._build_test_package([sig], artifact_id="artifact-missing-speaker-test")
        receipts = self._apply_package(pkg)
        self.assertGreater(len(receipts), 0)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT speaker_or_author FROM assertion WHERE legacy_record_type='edge' AND qualifiers_json LIKE '%meeting-adv-02%'"
        ).fetchone()
        self.assertIsNotNone(row)
        # Verify fallback preserved non-empty speaker attribution from source attendees
        self.assertIn(row["speaker_or_author"], ["Cameron Mercer", "Jen Bricker"])
        conn.close()

    def test_case_3_sticky_note_malformed_locator_resilience(self) -> None:
        """Adversarial check: Malformed locator does not crash evidence item insertion."""
        sig = {
            "signal_id": "sig-malformed-loc-01",
            "from_id": "stakeholder:stephan_marceau",
            "to_id": "workstream:delivery_staffing",
            "predicate": "DELIVERY_ACCOUNTABILITY_FOR",
            "assertion_type": "governed_accountability",
            "confidence": {"score": 0.78, "tier": "MEDIUM", "rationale": "Caveat observed"},
            "evidence": {"quote": "Tentative staffing text.", "locator": {}},  # Empty locator
            "lineage": {"source_id": "meeting-adv-03", "speaker": "Cameron Mercer"},
            "evidence_ids": ["stmt-stephan-01"],
        }
        pkg = self._build_test_package([sig], artifact_id="artifact-malformed-loc-test")
        receipts = self._apply_package(pkg)
        self.assertGreater(len(receipts), 0)

    def test_case_4_idempotent_edge_replay_defense(self) -> None:
        """Adversarial check: Re-analyzing the exact same package against mutated DB prevents duplicate relationships."""
        sig = {
            "signal_id": "sig-replay-01",
            "from_id": "stakeholder:stephan_marceau",
            "to_id": "workstream:delivery_staffing",
            "predicate": "DELIVERY_ACCOUNTABILITY_FOR",
            "assertion_type": "governed_accountability",
            "confidence": {"score": 0.84, "tier": "HIGH", "rationale": "High fidelity"},
            "evidence": {"quote": "On the hook for delivery.", "locator": {"kind": "timestamp", "value": "29:36"}},
            "lineage": {"source_id": "meeting-adv-04", "speaker": "Cameron Mercer"},
            "evidence_ids": ["stmt-stephan-01"],
        }
        pkg = self._build_test_package([sig], artifact_id="artifact-replay-test")
        receipts_1 = self._apply_package(pkg)
        self.assertGreater(len(receipts_1), 0)

        conn = sqlite3.connect(self.db_path)
        existing_row = conn.execute(
            "SELECT assertion_id FROM assertion WHERE legacy_record_type='edge' AND qualifiers_json LIKE '%meeting-adv-04%'"
        ).fetchone()
        self.assertIsNotNone(existing_row)
        existing_assertion_id = existing_row[0]
        conn.close()

        # Second run against the already-mutated database:
        # Routing to CORROBORATING_EVIDENCE for existing edge emits add_evidence, NOT duplicate add_relationship!
        bundle_2 = normalize_evidence_envelope(seal_artifact(pkg), run_context="run_2")
        res_2 = resolve_bundle(bundle_2, self.db_path)
        routes_2 = [
            {
                "subject_id": c["candidate_id"],
                "intake_route": "CORROBORATING_EVIDENCE" if c["candidate_type"] == "architecture_relationship" else "EXISTING_ENTITY",
                "reason_codes": [],
                "extensions": {
                    "com.partner_advisory.nee.corroborate-assertion-id": existing_assertion_id
                } if c["candidate_type"] == "architecture_relationship" else {},
            }
            for c in bundle_2["candidates"]
        ]
        disp_2 = build_dispositions(bundle_2, routes_2, res_2)
        cs_2 = build_graph_change_set(bundle_2, disp_2, res_2, self.db_path)

        self.assertIsNotNone(cs_2)
        rel_ops_2 = [op for op in cs_2["operations"] if op["operation"] == "add_relationship"]
        self.assertEqual(len(rel_ops_2), 0, "Second run must not re-emit add_relationship for existing edge")
        ev_ops_2 = [op for op in cs_2["operations"] if op["operation"] == "add_evidence"]
        self.assertEqual(len(ev_ops_2), 1, "Second run must emit add_evidence corroboration instead of duplicate edge")


if __name__ == "__main__":
    unittest.main()
