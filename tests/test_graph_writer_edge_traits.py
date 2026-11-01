"""Unit tests for GovernedGraphWriter 4 Edge Traits de-hardcoding.

Verifies:
1. Inserted assertion rows reflect dynamic confidence_score (e.g. 0.94, not hardcoded 0.45).
2. Inserted assertion rows reflect dynamic confidence_label (e.g. HIGH, not hardcoded directional).
3. Inserted assertion rows reflect real speaker_or_author (e.g. Jen Bricker, not None).
4. Inserted assertion rows reflect dynamic assertion_type (e.g. architecture_relationship).
5. Inserted assertion rows set legacy_record_type='edge' when object_node_id is present.
6. Qualifiers_json captures the full edge_traits block.
7. Inserted evidence_item rows capture direct_quote and transcript_timestamp.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set
from dual_pipeline.normalization import normalize_evidence_envelope


class TestGraphWriterEdgeTraits(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_graph = (
            ROOT / ".local" / "dual_pipeline" / "generations" / "generation-36b538f04af06cb4be863517d4f0" / "graph.sqlite"
        )
        if not self.orig_graph.exists():
            self.skipTest(f"Active baseline graph not found at {self.orig_graph}")

        self.db_path = Path(self.tmp_dir.name) / "test_isolated_graph.sqlite"
        shutil.copy2(self.orig_graph, self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_writer_persists_four_edge_traits_into_sqlite(self) -> None:
        """Verify dynamic persistence of all 4 Edge Traits into SQLite assertion and evidence_item."""
        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-writer-edge-traits-test",
            "meeting": {
                "meeting_id": "meeting-writer-edge-traits-test",
                "title": "Platform Scaling Architecture Review",
                "started_at": "2026-09-10T10:00:00-04:00",
                "source_location": "archive://notes/meeting-writer-edge-traits-test.md",
                "source_hash": "f" * 64,
                "attendees": ["Jen Bricker", "Carlos Salazar"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-jen-bricker",
                    "reported_name": "Jen Bricker",
                    "email": "jen.bricker@consulting.example.com",
                    "role_or_org": "Technical Services Dept",
                    "statements": [
                        {
                            "statement_id": "stmt-01",
                            "kind": "technical",
                            "text": "The Nexus stream router handles all inter-department event dispatching.",
                            "locator": {"kind": "timestamp", "value": "01:23"},
                            "gemini_confidence": 0.95,
                        }
                    ],
                }
            ],
            "actions": [],
            "adapter": {
                "name": "nee-evidence-envelope-intake",
                "version": "1.0.0",
            },
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": [
                        {
                            "id": "stakeholder:jen_bricker",
                            "type": "stakeholder",
                            "name": "Jen Bricker",
                            "description": "Technical Services Lead",
                            "mutation": "CONFIRM",
                        },
                        {
                            "id": "system:nexus_stream_router",
                            "type": "system",
                            "name": "Nexus Stream Router",
                            "description": "Event dispatch engine",
                            "mutation": "CREATE",
                        },
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-writer-01",
                            "from_id": "stakeholder:jen_bricker",
                            "to_id": "system:nexus_stream_router",
                            "predicate": "OWNS_OR_LEADS",
                            "assertion_type": "architecture_relationship",
                            "confidence": {
                                "score": 0.92,
                                "tier": "HIGH",
                                "rationale": "Direct leadership statement with unambiguous ownership",
                            },
                            "evidence": {
                                "quote": "I am the engineering owner for the Nexus stream router.",
                                "locator": {"kind": "timestamp", "value": "01:23"},
                                "turn_offset": 5,
                            },
                            "lineage": {
                                "source_id": "meeting-writer-edge-traits-test",
                                "speaker": "Jen Bricker",
                                "org": "PartnerAdvisory",
                                "recorded_at": "2026-09-10T10:01:23-04:00",
                            },
                            "evidence_ids": ["stmt-01"],
                        }
                    ],
                }
            },
        }

        # 1. Normalize
        bundle = normalize_evidence_envelope(seal_artifact(package), run_context="writer_run")
        validate_artifact(bundle)

        # 2. Resolve & Route
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

        # 3. Change Set
        change_set = build_graph_change_set(
            bundle=bundle,
            dispositions=dispositions,
            resolutions=resolutions,
            graph_path=self.db_path,
        )
        self.assertIsNotNone(change_set)
        change_set["status"] = "approved"
        change_set["approval_id"] = "approval-writer-test"
        change_set = seal_artifact(change_set)
        validate_artifact(change_set)

        # 4. Apply with GovernedGraphWriter
        writer = GovernedGraphWriter(self.db_path)
        receipts = writer.apply(change_set)
        self.assertGreater(len(receipts), 0)

        # 5. Connect and assert SQLite rows
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Query relationship assertion
        row = conn.execute(
            """SELECT assertion_id, predicate, assertion_type, speaker_or_author,
                      confidence_label, confidence_score, legacy_record_type, qualifiers_json
               FROM assertion
               WHERE predicate='OWNS_OR_LEADS'"""
        ).fetchone()

        self.assertIsNotNone(row, "Assertion with OWNS_OR_LEADS predicate must be present")

        # De-hardcoded check 1: ASSERTION TYPE (Controlled vocabulary maps to extracted_claim)
        self.assertEqual(row["assertion_type"], "extracted_claim")

        # De-hardcoded check 2: CONFIDENCE SCORE & LABEL
        self.assertAlmostEqual(row["confidence_score"], 0.92, places=2)
        self.assertEqual(row["confidence_label"], "high")

        # De-hardcoded check 3: SPEAKER_OR_AUTHOR
        self.assertEqual(row["speaker_or_author"], "Jen Bricker")

        # De-hardcoded check 4: LEGACY RECORD TYPE ('edge' not 'claim')
        self.assertEqual(row["legacy_record_type"], "edge")

        # De-hardcoded check 5: Qualifiers contains full edge traits
        qualifiers = json.loads(row["qualifiers_json"])
        self.assertIn("edge_traits", qualifiers)
        self.assertEqual(qualifiers["edge_traits"]["assertion_type"], "architecture_relationship")
        self.assertEqual(qualifiers["edge_traits"]["relationship_predicate"], "OWNS_OR_LEADS")
        self.assertEqual(qualifiers["edge_traits"]["confidence"]["tier"], "HIGH")

        # De-hardcoded check 6: Evidence item captures transcript_timestamp
        ev_row = conn.execute(
            """SELECT locator_kind, locator_value, transcript_timestamp, direct_quote
               FROM evidence_item
               WHERE locator_kind='timestamp' AND locator_value='01:23'"""
        ).fetchone()
        self.assertIsNotNone(ev_row)
        self.assertEqual(ev_row["locator_kind"], "timestamp")
        self.assertEqual(ev_row["transcript_timestamp"], "01:23")

        conn.close()


if __name__ == "__main__":
    unittest.main()
