"""End-to-End Test for Wave 4: Source Grounding, Provenance Enforcement & Fixture Containment.

Validates the full post-processing pipeline for Deliverable 03 (Canonical Signal Projection):
1. Verifies that non-LENS projects (e.g. project_nexus) never receive LENS/NEA entity leaks.
2. Verifies that quote pointers are strictly resolved against Deliverable 01 forensic text.
3. Verifies GS-10 confidence defaults to 0.50 LOW (uncalibrated) instead of fabricated 0.90 HIGH.
4. Verifies temporal dates derive from metadata (MM/DD/YYYY) without hardcoded date injection.
5. Verifies attendee provenance propagation through scaffolding and roster records.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata
from dual_pipeline.transcript_stages_core.graph_executor import _enrich_gs10_traits
from dual_pipeline.transcript_stages_core.graph_post_sanitizer import (
    sanitize_projection_payload,
)
from dual_pipeline.transcript_stages_core.quote_pointer_resolver import (
    resolve_evidence_pointers,
)

FORENSIC_TRANSCRIPT_NEXUS = (
    "## 01: Forensic Meeting Transcript - DocIQ Architecture Sync\n\n"
    "[00:01:05] Jack Miller: Welcome everyone. We are here to discuss DocIQ ingestion scalability.\n"
    "[00:01:25] Amit Ayer: The primary constraint is document parsing latency across large PDFs.\n"
    "[00:01:50] Jack Miller: We should consider adding a caching tier for parsed document layouts.\n"
    "[00:02:15] Amit Ayer: Agreed. That will decouple layout analysis from extraction logic.\n"
)


class Wave4GroundingE2ETests(unittest.TestCase):
    """Exercises full end-to-end sanitization, grounding, and provenance flow."""

    def test_end_to_end_nexus_projection_grounding_and_containment(self):
        # 1. Simulate raw extracted projection payload from LLM
        raw_projection: Dict[str, Any] = {
            "schema_version": "1.0.0",
            "meeting_id": "nexus-dociq-sync-10-15-2026",
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "speaker": "Jack Miller",
                    "text": "The primary constraint is document parsing latency across large PDFs.",
                    "locator": {"kind": "timestamp", "value": "00:01:25"},
                },
                "ev-02": {
                    "statement_id": "ev-02",
                    "speaker": "Amit Ayer",
                    "text": "This quote was hallucinated by the model and does not exist.",
                    "locator": {"kind": "timestamp", "value": "00:02:15"},
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:dociq",
                        "type": "system",
                        "name": "DocIQ",
                        "description": "Intelligent document processing system.",
                        "evidence_ids": ["ev-01"],
                    },
                    {
                        # Banned pseudo-system attempting to enter
                        "id": "system:review-queue",
                        "type": "system",
                        "name": "Review Queue",
                    },
                ],
                "relationship_signals": [
                    {
                        "signal_id": "rel-dociq--cache--DEPENDS_ON",
                        "from_id": "system:dociq",
                        "to_id": "system:cache-tier",
                        "relationship": "DEPENDS_ON",
                        "evidence_ids": ["ev-01"],
                    }
                ],
            },
            "decision_updates": [
                {
                    "decision_id": "dec:add-caching-tier",
                    "title": "Add Caching Tier",
                    "status": "APPROVED",
                    "observed_at": "2026-10-15",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "risk_updates": [
                {
                    "risk_id": "risk:parsing-latency",
                    "title": "Parsing Latency Risk",
                    "status": "ACTIVE",
                    "observed_at": "10/15/2026",
                    "evidence_ids": ["ev-01"],
                }
            ],
        }

        metadata = {
            "project": "project_nexus",
            "meeting_name": "DocIQ_Architecture_Sync",
            "date": "10/15/2026",
            "meeting_date": "10/15/2026",
            "observed_at": "10/15/2026",
        }

        # 2. Run Quote Pointer Resolution
        resolve_evidence_pointers(raw_projection, FORENSIC_TRANSCRIPT_NEXUS)

        ev1 = raw_projection["evidence_index"]["ev-01"]
        ev2 = raw_projection["evidence_index"]["ev-02"]

        self.assertEqual(ev1["grounding_status"], "GROUNDED")
        self.assertIsNotNone(ev1["quote_pointer"])
        self.assertEqual(ev1["exact_text"], "The primary constraint is document parsing latency across large PDFs.")

        self.assertEqual(ev2["grounding_status"], "UNGROUNDED_HALLUCINATION")
        self.assertIsNone(ev2["quote_pointer"])
        self.assertNotIn("exact_text", ev2)

        # 3. Run Projection Sanitizer & Healer with project="project_nexus"
        sanitized = sanitize_projection_payload(
            raw_projection,
            source_text=FORENSIC_TRANSCRIPT_NEXUS,
            project="project_nexus",
        )

        # 4. Run GS-10 Trait Enrichment
        _enrich_gs10_traits(sanitized, metadata)

        # 5. Verify Invariants
        # Invariant A: Fixture containment - No LENS or NEA entities injected into Nexus
        entity_ids = {e["id"] for e in sanitized["graph_mutations"]["entity_upserts"]}
        self.assertIn("system:dociq", entity_ids)
        self.assertNotIn("system:review-queue", entity_ids)
        self.assertNotIn("system:lens", entity_ids)
        self.assertNotIn("system:lens-rule-builder", entity_ids)
        self.assertNotIn("system:lens-rule-registry", entity_ids)
        self.assertNotIn("org:nea", entity_ids)

        # Invariant B: GS-10 Confidence is 0.50 LOW (uncalibrated), NOT fabricated 0.90 HIGH
        rel = sanitized["graph_mutations"]["relationship_signals"][0]
        self.assertEqual(rel["confidence"]["score"], 0.50)
        self.assertEqual(rel["confidence"]["tier"], "LOW")
        self.assertIn("Uncalibrated", rel["confidence"]["rationale"])

        # Invariant C: Lineage recorded_at is taken from metadata (10/15/2026), NOT hardcoded 2026-10-01
        self.assertEqual(rel["lineage"]["recorded_at"], "10/15/2026")

        # Invariant D: Date format normalized to MM/DD/YYYY
        dec = sanitized["decision_updates"][0]
        self.assertEqual(dec["observed_at"], "10/15/2026")
        self.assertTrue(dec["decision_id"].startswith("decision:"))

        rsk = sanitized["risk_updates"][0]
        self.assertEqual(rsk["observed_at"], "10/15/2026")
        self.assertTrue(rsk["risk_id"].startswith("risk:"))

    def test_attendee_provenance_propagation_in_scaffolding(self):
        with tempfile.TemporaryDirectory() as td:
            transcript_file = Path(td) / "meeting_transcript.md"
            transcript_content = (
                "# Engineering Architecture Review\n\n"
                "Date: 10/12/2026\n\n"
                "## Attendees\n"
                "- Sarah Connor (Cyberdyne) - Tech Lead\n"
                "- John Connor (Resistance) - Commander\n\n"
                "## Discussion\n"
                "[00:01:00] Sarah Connor: We need to evaluate the core architecture.\n"
            )
            transcript_file.write_text(transcript_content, encoding="utf-8")

            meta = scaffold_meeting_metadata(
                input_path=transcript_file,
                project="project_skynet",
                classification="confidential",
            )

            self.assertEqual(meta["project"], "project_skynet")
            self.assertIn("roster", meta)
            self.assertGreaterEqual(len(meta["roster"]), 2)
            names = {r["name"] for r in meta["roster"]}
            self.assertIn("Sarah Connor", names)
            self.assertIn("John Connor", names)
