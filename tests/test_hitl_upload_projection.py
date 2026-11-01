"""
tests/test_hitl_upload_projection.py

Unit tests for Wave 4: Enforce factual coverage through Output 03 and normalization.
Verifies:
1. Typed fact coverage detects dropped eligible facts before proposal authorization.
2. Explicit exclusions and quarantines require supported reasons.
3. Unsupported predicates are quarantined and never silently converted to generic relationships.
4. Collateral healer preserves document references without inventing synthetic domain edges.
5. Prompt builder eliminates blanket judge waivers in favor of content-level grounding.
6. Orphan pruning protects authenticated and collateral entities from deletion.
7. Deliverable changes invalidate prior proposal digests.
"""
from __future__ import annotations

import json
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
    verify_fact_coverage_in_projection,
)
from dual_pipeline.transcript_stages_core.graph_post_helpers import (
    _collect_connected_ids,
    _normalize_predicate,
    _normalize_single_relationship,
    _prune_orphan_entities,
)
from dual_pipeline.transcript_qa_core.collateral_healer import (
    _upsert_single_collateral_graph_mutation,
    heal_collateral_graph_mutations,
)
from dual_pipeline.attachment_core.prompt_builder import (
    build_attachment_prompt_section,
    _format_collateral_reviewed_block,
)
from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    compute_proposal_digest_from_deliverables,
)


class TestHITLUploadProjection:
    """Unit test suite for Wave 4 fact coverage and projection invariants."""

    def test_evidence_coverage_verification_detects_dropped_eligible_fact(self):
        """When an eligible fact is omitted from Output 03 without reason, verification fails."""
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-unit-01",
            run_id="run-unit-01",
            source_digest="a" * 64,
            items=[
                EvidenceItem(
                    item_id="item-role-1",
                    item_type=ItemType.SPEAKER_ROLE.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.EXTRACTED.value,
                    source_origin={"source_type": SourceOriginType.TRANSCRIPT_HITL.value, "source_hash": "a" * 64},
                    disposition_reason="Operator confirmed role as Lead Architect",
                    proposed_fact={"subject": "person:alice-smith", "predicate": "HAS_ROLE", "object": "role:lead-architect"},
                )
            ],
        )

        empty_projection: Dict[str, Any] = {
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_index": {},
        }

        is_valid, violations, stats = verify_fact_coverage_in_projection(empty_projection, manifest)
        assert is_valid is False
        assert len(violations) == 1
        assert "item-role-1" in violations[0]
        assert "was removed during projection or healing" in violations[0]
        assert stats["missing_without_reason"] == 1

    def test_evidence_coverage_verification_passes_when_fact_grounded(self):
        """When an eligible fact is represented in Output 03, item state transitions to PROPOSED."""
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-unit-02",
            run_id="run-unit-02",
            source_digest="b" * 64,
            items=[
                EvidenceItem(
                    item_id="item-person-1",
                    item_type=ItemType.PERSON_IDENTITY.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.EXTRACTED.value,
                    source_origin={"source_type": SourceOriginType.TRANSCRIPT_HITL.value, "source_hash": "b" * 64},
                    disposition_reason="Operator affirmed person identity",
                    proposed_fact={"subject": "person:bob-jones", "statement_kind": StatementKind.ENTITY_UPSERT.value},
                )
            ],
        )

        grounded_projection: Dict[str, Any] = {
            "graph_mutations": {
                "entity_upserts": [{"id": "person:bob-jones", "name": "Bob Jones", "type": "person"}],
                "relationship_signals": [],
            },
            "evidence_index": {},
        }

        is_valid, violations, stats = verify_fact_coverage_in_projection(grounded_projection, manifest)
        assert is_valid is True
        assert len(violations) == 0
        assert stats["covered_in_projection"] == 1
        assert manifest.items[0].state == EvidenceState.PROPOSED.value

    def test_evidence_coverage_allows_explicit_exclusions_with_reason(self):
        """Excluded or quarantined items with supported reasons do not cause coverage failure."""
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-unit-03",
            run_id="run-unit-03",
            source_digest="c" * 64,
            items=[
                EvidenceItem(
                    item_id="item-demo-finance",
                    item_type=ItemType.TABLE_RECORD.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.EXCLUDED.value,
                    source_origin={"source_type": SourceOriginType.SCREENSHOT_ATTACHMENT.value, "source_hash": "c" * 64},
                    disposition_reason="Demo / sample financial mockup excluded from canonical facts",
                ),
                EvidenceItem(
                    item_id="item-quarantined",
                    item_type=ItemType.VISUAL_CLAIM.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.QUARANTINED.value,
                    source_origin={"source_type": SourceOriginType.SCREENSHOT_ATTACHMENT.value, "source_hash": "c" * 64},
                    disposition_reason="OCR engine failure; quarantined pending rerun",
                ),
            ],
        )

        projection: Dict[str, Any] = {"graph_mutations": {"entity_upserts": []}, "evidence_index": {}}
        is_valid, violations, stats = verify_fact_coverage_in_projection(projection, manifest)
        assert is_valid is True
        assert stats["explicitly_excluded"] == 1
        assert stats["explicitly_quarantined"] == 1

    def test_unsupported_predicate_quarantined_not_silently_converted(self):
        """Unsupported predicates must be quarantined rather than silently masked as CONTRIBUTES_TO."""
        rel: Dict[str, Any] = {
            "from_id": "system:core",
            "to_id": "system:gateway",
            "relationship": "ARBITRARY_CUSTOM_LINK",
        }

        _normalize_single_relationship(rel)

        assert rel.get("is_quarantined") is True
        assert "Unsupported" in rel.get("quarantine_reason", "")
        assert rel.get("relationship") != "CONTRIBUTES_TO"
        assert rel.get("relationship", "").startswith("UNSUPPORTED:")

    def test_collateral_healer_preserves_document_without_synthetic_integrates_with(self):
        """Reviewed collateral without semantic components is preserved as a document without INTEGRATES_WITH."""
        data: Dict[str, Any] = {
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_index": {},
        }
        metadata: Dict[str, Any] = {
            "project": "core-platform",
            "attachments": [
                {
                    "filename": "quarterly_presentation.pdf",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "title": "Quarterly Deck",
                    # No semantic components!
                }
            ],
        }
        changes: list[str] = []

        heal_collateral_graph_mutations(data, metadata, changes)

        entities = data["graph_mutations"]["entity_upserts"]
        signals = data["graph_mutations"]["relationship_signals"]

        # Entity should be preserved as type "document"
        doc_ents = [e for e in entities if e.get("type") == "document"]
        assert len(doc_ents) == 1
        assert doc_ents[0]["id"] == "document:quarterly-presentation"

        # No synthetic system entity should be created
        sys_ents = [e for e in entities if e.get("id") == "system:quarterly-presentation"]
        assert len(sys_ents) == 0

        # No synthetic INTEGRATES_WITH edge should be added
        iw_signals = [s for s in signals if s.get("predicate") == "INTEGRATES_WITH" and "quarterly-presentation" in s.get("from_id", "")]
        assert len(iw_signals) == 0

    def test_collateral_healer_with_semantic_components_adds_system_and_signal(self):
        """Reviewed collateral WITH semantic components creates structured system and connection."""
        data: Dict[str, Any] = {
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_index": {},
        }
        metadata: Dict[str, Any] = {
            "project": "core-platform",
            "attachments": [
                {
                    "filename": "pipeline_architecture.png",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "title": "Pipeline Architecture",
                    "components": [{"id": "c1", "label": "Ingestion Engine"}],
                }
            ],
        }
        changes: list[str] = []

        heal_collateral_graph_mutations(data, metadata, changes)

        entities = data["graph_mutations"]["entity_upserts"]
        signals = data["graph_mutations"]["relationship_signals"]

        sys_ents = [e for e in entities if e.get("id") == "system:pipeline-architecture"]
        assert len(sys_ents) == 1
        assert sys_ents[0]["type"] == "system"

        iw_signals = [s for s in signals if s.get("from_id") == "system:pipeline-architecture"]
        assert len(iw_signals) == 1
        assert iw_signals[0]["predicate"] == "INTEGRATES_WITH"

    def test_prompt_builder_grounding_rule_no_blanket_waiver(self):
        """Prompt builder must not emit blanket judge waivers."""
        record = AttachmentRecord(
            attachment_id="att-test-1",
            original_path=Path("/tmp/chart.png"),
            staged_path=Path("/tmp/chart.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="chart.png",
            file_size_bytes=100,
            sha256="1" * 64,
            attribution_mode="COLLATERAL_REVIEWED",
        )
        content = ExtractedAttachmentContent(
            attachment_id="att-test-1",
            filename="chart.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="1" * 64,
            title="Chart",
            extracted_text="Content text",
        )

        prompt = build_attachment_prompt_section(
            attachments=[record],
            extracted_contents={record.attachment_id: content},
        )

        # Blanket waiver must NOT be present
        assert "The LLM judge MUST NOT mark such claims as UNSUPPORTED" not in prompt
        assert "Do NOT mark as UNSUPPORTED" not in prompt
        # Content-level grounding must be required
        assert "GROUNDING RULE" in prompt
        assert "ground in concrete extracted visual text" in prompt

    def test_orphan_pruning_preserves_authenticated_and_reviewed_entities(self):
        """_collect_connected_ids protects authenticated, reviewed, and eligible entities from orphan pruning."""
        entities = [
            {"id": "person:approved-operator", "name": "Approved Operator", "is_authenticated": True},
            {"id": "document:reviewed-spec", "name": "Reviewed Spec", "attribution_mode": "COLLATERAL_REVIEWED"},
            {"id": "system:orphan-unapproved", "name": "Unapproved Orphan"},
        ]
        mutations = {"entity_upserts": entities, "relationship_signals": []}

        connected = _collect_connected_ids([], mutations)
        assert "person:approved-operator" in connected
        assert "document:reviewed-spec" in connected
        assert "system:orphan-unapproved" not in connected

        pruned = _prune_orphan_entities(entities, connected)
        pruned_ids = [e["id"] for e in pruned]
        assert "person:approved-operator" in pruned_ids
        assert "document:reviewed-spec" in pruned_ids
        assert "system:orphan-unapproved" not in pruned_ids

    def test_changed_deliverables_invalidates_proposal_digest(self):
        """Rebuilding a changed proposal produces a different digest, invalidating earlier approval."""
        d1 = compute_proposal_digest_from_deliverables(
            meeting_name="Weekly Alignment",
            source_hash="1" * 64,
            output_hashes={"01": "a" * 64, "02": "b" * 64, "03": "c" * 64},
            intake_hash="2" * 64,
        )

        # Output 03 changed because an eligible fact was added/corrected
        d2 = compute_proposal_digest_from_deliverables(
            meeting_name="Weekly Alignment",
            source_hash="1" * 64,
            output_hashes={"01": "a" * 64, "02": "b" * 64, "03": "f" * 64},  # Changed!
            intake_hash="2" * 64,
        )

        assert d1 != d2
