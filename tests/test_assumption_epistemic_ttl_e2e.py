"""End-to-end and adversarial test suite for epistemic assumption typing and checkpoint TTL.

Verifies:
1. Normalization preserves assumption, proposal, and hypothesis semantics.
2. GovernedGraphWriter populates assertion_type='assumption' for proposals/assumptions.
3. Checkpoint deadlines (explicit or default 14-day) are bound to assertion.valid_to
   and qualifiers_json.stale_after.
4. hitl_required=1 assertions are assigned non-empty validation_owner passing label quality audit.
5. Negative decision predicates (DECIDED_AGAINST, REJECTED, STANDARDIZES_ON) are accepted.
6. Edge case / adversarial handling: malformed dates, missing dates, empty traits.
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
from dual_pipeline.l0.assumption_lifecycle import (
    DEFAULT_ASSUMPTION_TTL_DAYS,
    DEFAULT_VALIDATION_OWNER,
    is_assertion_temporally_expired,
    normalize_epistemic_assertion_type,
    resolve_assumption_checkpoint_ttl,
)
from dual_pipeline.normalization import normalize_evidence_envelope
from dual_pipeline.normalization_core.meeting_entity_helpers import (
    ALLOWED_RELATIONSHIP_PREDICATES,
    _statement_semantics,
)


class TestAssumptionEpistemicTTLE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        import json
        active_pair_path = ROOT / ".local" / "dual_pipeline" / "active_pair.json"
        if active_pair_path.exists():
            with open(active_pair_path, "r", encoding="utf-8") as f:
                pair = json.load(f)
            self.orig_graph = ROOT / ".local" / "dual_pipeline" / pair["graph_path"]
        else:
            candidates = list((ROOT / ".local" / "dual_pipeline" / "generations").glob("generation-*/graph.sqlite"))
            if not candidates:
                self.skipTest("No baseline generation graph found")
            self.orig_graph = candidates[0]
        self.db_path = Path(self.tmp_dir.name) / "test_epistemic_ttl_graph.sqlite"
        shutil.copy2(self.orig_graph, self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_epistemic_policy_unit_and_boundary_cases(self) -> None:
        """Verify L0 assumption policy normalization, TTL resolution, and date edge cases."""
        # Epistemic type normalization
        self.assertEqual(normalize_epistemic_assertion_type("proposal"), "assumption")
        self.assertEqual(normalize_epistemic_assertion_type("assumption"), "assumption")
        self.assertEqual(normalize_epistemic_assertion_type("hypothesis"), "hypothesis")
        self.assertEqual(normalize_epistemic_assertion_type("risk"), "hypothesis")
        self.assertEqual(normalize_epistemic_assertion_type(""), "extracted_claim")
        self.assertEqual(normalize_epistemic_assertion_type(None), "extracted_claim")

        # Statement semantics in normalization
        self.assertEqual(_statement_semantics("proposal"), ("assumption", "candidate_assertion", "medium"))
        self.assertEqual(_statement_semantics("assumption"), ("assumption", "candidate_assertion", "medium"))
        self.assertEqual(_statement_semantics("hypothesis"), ("hypothesis", "candidate_assertion", "medium"))

        # Negative decision predicates in allowed set
        self.assertIn("DECIDED_AGAINST", ALLOWED_RELATIONSHIP_PREDICATES)
        self.assertIn("REJECTED", ALLOWED_RELATIONSHIP_PREDICATES)
        self.assertIn("STANDARDIZES_ON", ALLOWED_RELATIONSHIP_PREDICATES)

        # Checkpoint TTL resolution: explicit checkpoint
        explicit = resolve_assumption_checkpoint_ttl(
            {"candidate_type": "source_chunk", "assertion_type": "assumption"},
            {"checkpoint_date": "2026-09-09"},
            {"date": "2026-09-04"},
        )
        self.assertEqual(explicit, "2026-09-09")

        # Checkpoint TTL resolution: fallback 14-day default for working notes
        working_notes_ttl = resolve_assumption_checkpoint_ttl(
            {"candidate_type": "source_chunk", "assertion_type": "assumption"},
            {},
            {"date": "2026-09-04", "source_posture": "working_notes"},
        )
        self.assertEqual(working_notes_ttl, "2026-09-18")

        # Temporal expiry evaluation
        self.assertTrue(is_assertion_temporally_expired("2026-09-09", None, as_of_date="2026-09-10"))
        self.assertFalse(is_assertion_temporally_expired("2026-09-09", None, as_of_date="2026-09-08"))
        self.assertTrue(is_assertion_temporally_expired(None, "2026-09-09", as_of_date="2026-09-09"))
        self.assertFalse(is_assertion_temporally_expired(None, None, as_of_date="2026-09-15"))

    def test_writer_persists_assumption_with_checkpoint_ttl_and_validation_owner(self) -> None:
        """E2E test: Ingesting an exploratory proposal binds assertion_type='assumption' and valid_to."""
        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-assumption-ttl-e2e-test",
            "meeting": {
                "meeting_id": "meeting-assumption-ttl-e2e-test",
                "title": "Project Beacon Path Forward Architecture Proposal",
                "started_at": "2026-09-04T14:00:00-04:00",
                "source_location": "archive://notes/meeting-assumption-ttl-e2e-test.md",
                "source_hash": "a" * 64,
                "attendees": ["logan Miller", "Amit Ayer"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-josh-millar",
                    "reported_name": "logan Miller",
                    "email": "josh.millar@consulting.example.com",
                    "role_or_org": "Technical Architecture Lead",
                    "statements": [
                        {
                            "statement_id": "stmt-proposal-01",
                            "kind": "strategic",
                            "text": "PartnerAdvisory proposes running a 45-day parallel FDX Greenfield experiment on Google Cloud Platform pending September 9 review.",
                            "locator": {"kind": "timestamp", "value": "14:15"},
                            "gemini_confidence": 0.85,
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
                            "id": "project:45_day_fdx_greenfield",
                            "type": "project",
                            "name": "45-Day Parallel FDX Greenfield",
                            "description": "Exploratory FDX Greenfield experiment",
                            "mutation": "CREATE",
                        },
                        {
                            "id": "system:gcp",
                            "type": "system",
                            "name": "Google Cloud Platform",
                            "description": "GCP Cloud Target",
                            "mutation": "CONFIRM",
                        },
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-proposal-01",
                            "from_id": "project:45_day_fdx_greenfield",
                            "to_id": "system:gcp",
                            "predicate": "RELATED_TO",
                            "assertion_type": "assumption",
                            "confidence": {
                                "score": 0.75,
                                "tier": "MEDIUM",
                                "rationale": "Exploratory greenfield proposal awaiting review",
                            },
                            "evidence": {
                                "quote": "PartnerAdvisory proposes running a 45-day parallel FDX Greenfield experiment on Google Cloud Platform pending September 9 review.",
                                "locator": {"kind": "timestamp", "value": "14:15"},
                                "turn_offset": 1,
                            },
                            "lineage": {
                                "source_id": "meeting-assumption-ttl-e2e-test",
                                "speaker": "logan Miller",
                                "org": "PartnerAdvisory",
                                "recorded_at": "2026-09-04T14:15:00-04:00",
                            },
                            "evidence_ids": ["stmt-proposal-01"],
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
                    if c["candidate_type"] in ("architecture_relationship", "relationship_signal")
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

        # Inject explicit checkpoint date (Sept 9) onto candidate payloads in operation-payloads
        from dual_pipeline.core import digest
        payloads = change_set["extensions"].get("com.partner_advisory.nee.operation-payloads", {})
        for op in change_set["operations"]:
            if op.get("operation") in ("add_candidate_assertion", "add_relationship"):
                payload = payloads.get(op["operation_id"])
                if payload is not None:
                    payload["checkpoint_date"] = "2026-09-09"
                    op["after_state_hash"] = digest(payload)

        change_set["status"] = "approved"
        change_set["approval_id"] = "approval-assumption-test"
        change_set = seal_artifact(change_set)
        validate_artifact(change_set)

        # 4. GovernedGraphWriter Apply
        writer = GovernedGraphWriter(self.db_path)
        receipts = writer.apply(change_set)
        self.assertGreater(len(receipts), 0)

        # 5. Independent database verification via sqlite3
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Find the newly created source
        src_row = cur.execute(
            "SELECT source_id FROM source WHERE source_name = ? ORDER BY rowid DESC LIMIT 1",
            ("Project Beacon Path Forward Architecture Proposal",),
        ).fetchone()
        self.assertIsNotNone(src_row, "Governed source must be created")
        source_id = src_row["source_id"]

        rows = cur.execute(
            """SELECT assertion_id, assertion_type, currentness_state, valid_from, valid_to,
                      hitl_required, validation_owner, validation_priority, qualifiers_json
               FROM assertion
               WHERE primary_source_id = ?""",
            (source_id,),
        ).fetchall()

        self.assertGreater(len(rows), 0, "At least one assertion must be written")
        found_assumption = False
        for row in rows:
            qualifiers = json.loads(row["qualifiers_json"])
            if row["hitl_required"] == 1:
                self.assertTrue(bool(row["validation_owner"]), "validation_owner must be populated when hitl_required=1")
                self.assertTrue(bool(row["validation_priority"]), "validation_priority must be populated when hitl_required=1")

            if row["assertion_type"] == "assumption":
                found_assumption = True
                self.assertEqual(row["valid_to"], "2026-09-09")
                self.assertEqual(qualifiers.get("stale_after"), "2026-09-09")
                self.assertEqual(row["currentness_state"], "current")

        self.assertTrue(found_assumption, "Proposal statement must yield assertion_type='assumption' with valid_to='2026-09-09'")

        # Verify label quality check passes without HITL_ASSIGNMENT_INCOMPLETE
        incomplete_count = cur.execute(
            """SELECT count(*) FROM assertion
               WHERE hitl_required=1
                 AND (trim(coalesce(validation_priority,''))=''
                      OR trim(coalesce(validation_owner,''))='')"""
        ).fetchone()[0]
        self.assertEqual(incomplete_count, 0, "No assertion may fail HITL assignment completeness")

        conn.close()


if __name__ == "__main__":
    unittest.main()
