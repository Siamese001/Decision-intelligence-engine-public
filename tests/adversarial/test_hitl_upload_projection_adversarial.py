"""
tests/adversarial/test_hitl_upload_projection_adversarial.py

Adversarial and edge-case verification for Wave 4 factual coverage and projection invariants.
Covers 4 mandatory categories:
1. Boundary payload & malformed input (empty projections, missing keys, malformed manifests)
2. Invariant bypass attempts (generic "artifact reviewed" bypass, unsupported predicate evasion, stale approval digest)
3. Overflow & mutation budget (budget exhaustion with protected authenticated entities)
4. Exact production regression (Finding F6: filename-only collateral cannot manufacture system integrations)
"""
from __future__ import annotations

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
    verify_fact_coverage_in_projection,
)
from dual_pipeline.transcript_stages_core.graph_post_extractor import _enforce_mutation_budget
from dual_pipeline.transcript_stages_core.graph_post_helpers import (
    _normalize_single_relationship,
    _prune_orphan_entities,
    _collect_connected_ids,
)
from dual_pipeline.transcript_qa_core.collateral_healer import heal_collateral_graph_mutations
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    compute_proposal_digest_from_deliverables,
)


class TestHITLUploadProjectionAdversarial:
    """Adversarial suite for Wave 4 fact coverage and projection invariants."""

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload & malformed input
    # -------------------------------------------------------------------------

    def test_boundary_empty_projection_does_not_crash_coverage_audit(self):
        """Empty or completely missing projection structures return clear missing-fact violations."""
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-adv-empty",
            run_id="run-adv-empty",
            source_digest="0" * 64,
            items=[
                EvidenceItem(
                    item_id="item-bound-1",
                    item_type=ItemType.FACTUAL_CORRECTION.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.EXTRACTED.value,
                    source_origin={"source_type": SourceOriginType.TRANSCRIPT_HITL.value, "source_hash": "0" * 64},
                    disposition_reason="Operator corrected budget",
                    proposed_fact={"subject": "project:core-dev", "predicate": "HAS_BUDGET", "object": "metric:100k"},
                )
            ],
        )

        # 1. Null-like empty dictionary
        is_valid, violations, stats = verify_fact_coverage_in_projection({}, manifest)
        assert is_valid is False
        assert len(violations) == 1
        assert "item-bound-1" in violations[0]
        assert stats["missing_without_reason"] == 1

        # 2. None fields in proposed fact
        manifest.items[0].proposed_fact = {}
        is_valid, violations, _ = verify_fact_coverage_in_projection({}, manifest)
        assert is_valid is False

    def test_malformed_manifest_schema_validation(self):
        """Manifests with missing required fields or invalid state enums fail schema validation."""
        # Missing required source_digest
        bad_manifest = {
            "contract_id": "urn:nee:dual-pipeline:evidence-coverage:1.0.0",
            "schema_version": "1.0.0",
            "manifest_id": "cov-invalid",
            "run_id": "run-01",
            # "source_digest" missing!
            "items": [],
            "coverage_summary": {},
            "generated_at": "2026-10-08T00:00:00Z",
        }

        is_valid, errors = validate_evidence_coverage_payload(bad_manifest)
        assert is_valid is False
        assert any("source_digest" in err for err in errors)

    # -------------------------------------------------------------------------
    # Category 2: Invariant bypass attempts
    # -------------------------------------------------------------------------

    def test_invariant_bypass_generic_mention_fails_coverage(self):
        """Generic 'artifact reviewed' string or filename alone cannot satisfy fact-level coverage."""
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-adv-bypass",
            run_id="run-adv-bypass",
            source_digest="3" * 64,
            items=[
                EvidenceItem(
                    item_id="item-specific-metric",
                    item_type=ItemType.TABLE_RECORD.value,
                    is_eligible_graph_fact=True,
                    state=EvidenceState.EXTRACTED.value,
                    source_origin={
                        "source_type": SourceOriginType.SCREENSHOT_ATTACHMENT.value,
                        "source_hash": "3" * 64,
                        "locator": "attachment:performance_table.png",
                    },
                    disposition_reason="Confirmed table metric: 99.9% uptime",
                    proposed_fact={
                        "subject": "metric:uptime-sla",
                        "predicate": "EVALUATES",
                        "object": "system:core-service",
                    },
                )
            ],
        )

        # Projection has generic mention of the filename, but NOT the specific metric or entity
        projection = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:unrelated", "name": "Unrelated", "type": "system"}
                ],
                "relationship_signals": [],
            },
            "evidence_index": {
                "ev-generic": {
                    "statement_id": "ev-generic",
                    "text": "Artifact was reviewed during session: performance_table.png",
                    # No attachment locator or metric mention!
                }
            },
        }

        is_valid, violations, stats = verify_fact_coverage_in_projection(projection, manifest)
        assert is_valid is False
        assert any("item-specific-metric" in v for v in violations)
        assert stats["missing_without_reason"] == 1

    def test_invariant_bypass_unsupported_predicate_cannot_masquerade_as_contributes_to(self):
        """Unsupported predicates must never silently become generic relationships."""
        rel = {
            "from_id": "system:alpha",
            "to_id": "system:beta",
            "relationship": "SUPER_INVENTED_CONNECTION_PREDICATE",
            "predicate": "SUPER_INVENTED_CONNECTION_PREDICATE",
        }

        _normalize_single_relationship(rel)

        # Must NOT be masked as CONTRIBUTES_TO
        assert rel.get("relationship") != "CONTRIBUTES_TO"
        assert rel.get("predicate") != "CONTRIBUTES_TO"
        assert rel.get("is_quarantined") is True
        assert "Unsupported" in rel.get("quarantine_reason", "")

    def test_invariant_bypass_stale_approval_digest_rejected(self):
        """Modifying Output 03 deltas invalidates earlier approval digest."""
        initial_digest = compute_proposal_digest_from_deliverables(
            meeting_name="Sprint Planning",
            source_hash="5" * 64,
            output_hashes={"01": "1" * 64, "02": "2" * 64, "03": "3" * 64},
            intake_hash="4" * 64,
            deltas={"upsert_count": 5},
        )

        # Someone modified deltas or Output 03 deliverables
        tampered_digest = compute_proposal_digest_from_deliverables(
            meeting_name="Sprint Planning",
            source_hash="5" * 64,
            output_hashes={"01": "1" * 64, "02": "2" * 64, "03": "3" * 64},
            intake_hash="4" * 64,
            deltas={"upsert_count": 6},  # Changed!
        )

        assert initial_digest != tampered_digest

    # -------------------------------------------------------------------------
    # Category 3: Overflow & Mutation budget
    # -------------------------------------------------------------------------

    def test_overflow_mutation_budget_protects_authenticated_entities(self):
        """When mutation budget is exhausted, budget governor prunes signals but retains authenticated entities."""
        # 35 entities and 20 signals (exceeding budget of 30)
        entities = [
            {"id": f"system:filler-{i}", "name": f"Filler {i}", "type": "system"}
            for i in range(20)
        ]
        # Crucial authenticated entity
        auth_person = {
            "id": "person:verified-architect",
            "name": "Verified Architect",
            "type": "person",
            "is_authenticated": True,
        }
        entities.append(auth_person)

        signals = [
            {
                "signal_id": f"sig-{i}",
                "from_id": f"system:filler-{i}",
                "to_id": f"system:filler-{(i + 1) % 20}",
                "relationship": "DEPENDS_ON",
            }
            for i in range(15)
        ]

        mutations = {
            "entity_upserts": entities,
            "relationship_signals": signals,
        }

        _enforce_mutation_budget(mutations, max_budget=20)

        # Verified architect must be retained!
        remaining_ids = {e["id"] for e in mutations["entity_upserts"]}
        assert "person:verified-architect" in remaining_ids

    # -------------------------------------------------------------------------
    # Category 4: Exact production regression (Finding F6)
    # -------------------------------------------------------------------------

    def test_finding_f6_regression_no_synthetic_integrates_with(self):
        """
        Finding F6 regression:
        Collateral healing must NOT manufacture a synthetic system entity or INTEGRATES_WITH
        relationship to the project based on a filename alone.
        """
        data = {
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_index": {},
        }
        metadata = {
            "project": "jarvis-platform",
            "attachments": [
                {
                    "filename": "screenshot_dashboard_status.png",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "title": "Dashboard Screenshot",
                    # No semantic components!
                }
            ],
        }
        changes: list[str] = []

        heal_collateral_graph_mutations(data, metadata, changes)

        mutations = data["graph_mutations"]
        entities = mutations["entity_upserts"]
        signals = mutations["relationship_signals"]

        # In legacy code, this created system:screenshot-dashboard-status
        # In Wave 4, it MUST create document:screenshot-dashboard-status
        assert not any(e["id"] == "system:screenshot-dashboard-status" for e in entities)
        assert any(e["id"] == "document:screenshot-dashboard-status" for e in entities)

        # In legacy code, this added sig-att-screenshot-dashbo with INTEGRATES_WITH
        # In Wave 4, no domain integration edge is promoted without content evidence!
        assert not any(
            s.get("relationship") == "INTEGRATES_WITH" and "screenshot" in s.get("from_id", "")
            for s in signals
        )
