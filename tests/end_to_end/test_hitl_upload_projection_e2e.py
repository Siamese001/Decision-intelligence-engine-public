"""
tests/end_to_end/test_hitl_upload_projection_e2e.py

End-to-end integration tests for Wave 4: Fact coverage through Output 03 and normalization.
Verifies:
1. Full Stage 03 projection flow with EvidenceCoverageManifest verification and schema conformance.
2. Dropped eligible facts are caught by the post-processing coverage audit.
3. Collateral healing without components preserves document entities without synthetic domain edges,
   and unsupported predicates are cleanly quarantined.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.evidence_coverage import (
    ClarificationKind,
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
    SourceOriginType,
    StatementKind,
    validate_evidence_coverage_payload,
)
from dual_pipeline.transcript_stages_core.graph_executor import post_process_stage03_projection


class TestHITLUploadProjectionE2E:
    """E2E integration suite for Wave 4 factual coverage and projection pipeline."""

    def test_e2e_full_projection_with_evidence_manifest_audit(self):
        """Full flow: projection post-processing validates evidence manifest coverage."""
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-e2e-01",
            run_id="run-e2e-01",
            source_digest="1" * 64,
            items=[
                EvidenceItem(
                    item_id="item-pers-alice",
                    item_type=ItemType.PERSON_IDENTITY.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.EXTRACTED.value,
                    source_origin={
                        "source_type": SourceOriginType.TRANSCRIPT_HITL.value,
                        "source_hash": "1" * 64,
                        "clarification_kind": ClarificationKind.PERSON_CONFIRMATION.value,
                    },
                    disposition_reason="Operator confirmed Alice Smith as Platform Lead",
                    proposed_fact={
                        "subject": "person:alice-smith",
                        "statement_kind": StatementKind.ENTITY_UPSERT.value,
                    },
                ),
                EvidenceItem(
                    item_id="item-claim-latency",
                    item_type=ItemType.VISUAL_CLAIM.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.EXTRACTED.value,
                    source_origin={
                        "source_type": SourceOriginType.SCREENSHOT_ATTACHMENT.value,
                        "source_hash": "1" * 64,
                        "locator": "attachment:arch_diagram.png",
                    },
                    disposition_reason="Extracted architecture latency claim",
                    proposed_fact={
                        "subject": "system:ingestion-gateway",
                        "statement_kind": StatementKind.FACT.value,
                    },
                ),
            ],
        )

        # Validate manifest schema conformance
        is_schema_valid, schema_errs = validate_evidence_coverage_payload(manifest.to_dict())
        assert is_schema_valid is True, f"Schema validation errors: {schema_errs}"

        # Raw projection carrying the required facts
        raw_graph_data = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice-smith", "name": "Alice Smith", "type": "person", "is_authenticated": True},
                    {"id": "system:ingestion-gateway", "name": "Ingestion Gateway", "type": "system"},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-01",
                        "from_id": "person:alice-smith",
                        "to_id": "system:ingestion-gateway",
                        "relationship": "OWNS_OR_LEADS",
                        "evidence_ids": ["ev-01"],
                    }
                ],
            },
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "text": "Alice Smith owns Ingestion Gateway",
                    "source_locators": ["attachment:arch_diagram.png"],
                }
            },
        }

        metadata = {
            "project": "gateway-core",
            "meeting_id": "m-e2e-01",
            "meeting_date": "10/08/2026",
            "evidence_manifest": manifest,
        }
        forensic_transcript = "**Alice Smith**: [00:01:00] Ingestion Gateway is online."

        result_json = post_process_stage03_projection(
            json.dumps(raw_graph_data), metadata, forensic_transcript
        )
        final_dict = json.loads(result_json)

        # Ensure coverage audit ran and passed
        audit = final_dict.get("_coverage_audit")
        assert audit is not None
        assert audit["is_compliant"] is True
        assert len(audit["violations"]) == 0
        assert audit["coverage_stats"]["covered_in_projection"] == 2
        assert manifest.items[0].state == EvidenceState.PROPOSED.value

    def test_e2e_dropped_fact_detected_in_post_processing(self):
        """When an approved fact is dropped from raw graph, coverage audit flags violation."""
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-e2e-02",
            run_id="run-e2e-02",
            source_digest="2" * 64,
            items=[
                EvidenceItem(
                    item_id="item-missing-engineer",
                    item_type=ItemType.PERSON_IDENTITY.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.EXTRACTED.value,
                    source_origin={"source_type": SourceOriginType.TRANSCRIPT_HITL.value, "source_hash": "2" * 64},
                    disposition_reason="Confirmed attendee",
                    proposed_fact={"subject": "person:charlie-brown"},
                )
            ],
        )

        # Raw graph completely omits charlie-brown
        raw_graph_data = {
            "graph_mutations": {
                "entity_upserts": [{"id": "system:other", "name": "Other", "type": "system"}],
                "relationship_signals": [],
            },
            "evidence_index": {},
        }
        metadata = {
            "project": "gateway-core",
            "meeting_id": "m-e2e-02",
            "meeting_date": "10/08/2026",
            "evidence_manifest": manifest,
        }

        result_json = post_process_stage03_projection(
            json.dumps(raw_graph_data), metadata, "Transcript text without Charlie"
        )
        final_dict = json.loads(result_json)

        audit = final_dict.get("_coverage_audit")
        assert audit is not None
        assert audit["is_compliant"] is False
        assert any("item-missing-engineer" in v for v in audit["violations"])
        assert audit["coverage_stats"]["missing_without_reason"] == 1

    def test_e2e_collateral_healing_and_predicate_quarantine(self):
        """Collateral without components becomes document entity; unsupported predicate is quarantined."""
        raw_graph_data = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:gateway", "name": "Gateway", "type": "system"},
                    {"id": "system:auth", "name": "Auth", "type": "system"},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-unknown-pred",
                        "from_id": "system:gateway",
                        "to_id": "system:auth",
                        "relationship": "NON_STANDARD_OBSOLETE_LINK",
                        "evidence_ids": ["ev-01"],
                    }
                ],
            },
            "evidence_index": {
                "ev-01": {"statement_id": "ev-01", "text": "Gateway connects to auth"}
            },
        }

        metadata = {
            "project": "gateway-core",
            "meeting_id": "m-e2e-03",
            "meeting_date": "10/08/2026",
            "attachments": [
                {
                    "filename": "meeting_notes.docx",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "title": "Meeting Notes",
                    # No components!
                }
            ],
        }

        result_json = post_process_stage03_projection(
            json.dumps(raw_graph_data), metadata, "Transcript"
        )
        final_dict = json.loads(result_json)
        mutations = final_dict.get("graph_mutations", {})

        # 1. Attachment without components is document, not system
        doc_ents = [e for e in mutations.get("entity_upserts", []) if e.get("id") == "document:meeting-notes"]
        assert len(doc_ents) == 1
        assert doc_ents[0]["type"] == "document"

        # 2. No synthetic INTEGRATES_WITH was invented for meeting-notes
        synth_signals = [
            s for s in mutations.get("relationship_signals", [])
            if "meeting-notes" in s.get("from_id", "") and s.get("relationship") == "INTEGRATES_WITH"
        ]
        assert len(synth_signals) == 0

        # 3. Unsupported predicate was quarantined
        bad_sig = next(
            s for s in mutations.get("relationship_signals", []) if s.get("signal_id") == "sig-unknown-pred"
        )
        assert bad_sig.get("is_quarantined") is True
        assert bad_sig.get("relationship") != "CONTRIBUTES_TO"
        assert "UNSUPPORTED" in bad_sig.get("relationship", "")
