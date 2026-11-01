"""Adversarial and boundary test suite for Wave 1 Document-Mode Healers and Index Wiring.

Covers:
1. Invariant bypass attempt: Archetype hijacking prevention (DOCUMENT_MODE preserved).
2. Boundary & overflow: Mutation budget scaling for multi-slide documents (>= 15 slides -> 60 budget).
3. Exact production regression: Structural index retention before metadata sanitization.
4. Boundary payload: Document-grounded entities preserved without edges.
5. Invariant preservation: Spoken dialogue and narrative timestamp healers skipped.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List
import pytest

from dual_pipeline.canonical_pipeline.c0_pa_runner import execute_c0_pa
from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
from dual_pipeline.document_pipeline_core.executor import _build_document_user_content
from dual_pipeline.transcript_duration_helper import resolve_duration_mutation_budget
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection, _heal_mutation_budget
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import (
    heal_substantive_transcript_evidence,
    heal_neet_coverage,
    heal_projection_evidence_and_coverage,
)


def test_document_mode_archetype_preserved_against_general_hijacking():
    """Invariant bypass attempt: DOCUMENT_MODE must not collapse to ARCHITECTURE_DEEP_DIVE."""
    data = {
        "meeting_archetype": "DOCUMENT_MODE",
        "evidence_index": {
            "ev-01": {"locator": {"kind": "slide", "value": "Slide 1"}, "exact_text": "AMS 101 Training Overview"}
        },
        "mutations": {
            "entity_upserts": [
                {"id": "system:ams_core", "name": "AMS Core", "evidence_ids": ["ev-01"]}
            ],
            "relationship_signals": [],
        },
    }
    changes: List[str] = []
    meta = {"is_document": True, "title": "AMS 101 Training Slide Deck"}

    heal_graph_projection(data, changes, metadata=meta, output_1="")

    assert data.get("meeting_archetype") == "DOCUMENT_MODE"
    assert data["mutations"].get("meeting_archetype") == "DOCUMENT_MODE"
    assert not any("ARCHITECTURE_DEEP_DIVE" in c for c in changes)


def test_document_structural_index_retained_in_prompt_building():
    """Exact production regression: Structural index must be extracted before metadata sanitization."""
    units = [
        {"unit_id": "slide_1", "locator": "Slide 1", "unit_type": "slide", "content": "AMS 101 Title Slide: Welcome to AMS Training", "metadata": {"title": "Title Slide"}},
        {"unit_id": "slide_2", "locator": "Slide 2", "unit_type": "slide", "content": "Operating Model: L1 L2 L3 delivery structure", "metadata": {"title": "Operating Model"}},
    ]
    meta = {
        "is_document": True,
        "source_units": units,
        "title": "AMS Training",
    }
    doc_text = "AMS Training Overview slide content"

    user_content = _build_document_user_content(doc_text, meta)

    assert "<document_structural_index>" in user_content
    assert "</document_structural_index>" in user_content
    assert "Slide 1" in user_content or "slide_1" in user_content or "Title Slide" in user_content
    assert "Slide 2" in user_content or "slide_2" in user_content or "Operating Model" in user_content


def test_orphan_prune_exempts_document_grounded_entities():
    """Boundary payload: Document entities with valid evidence citations must not be pruned as orphans."""
    data = {
        "meeting_archetype": "DOCUMENT_MODE",
        "evidence_index": {
            "ev-slide-01": {
                "locator": {"kind": "slide", "value": "1"},
                "exact_text": "Apex Global Enterprises Delivery Management Platform",
            }
        },
        "mutations": {
            "entity_upserts": [
                {
                    "id": "concept:apex_global_delivery_model",
                    "name": "Apex Global Delivery Model",
                    "evidence_ids": ["ev-slide-01"],
                }
            ],
            "relationship_signals": [],
        },
    }
    changes: List[str] = []
    meta = {"is_document": True, "output3_only": True}

    heal_graph_projection(data, changes, metadata=meta, output_1="")

    retained_ids = [e["id"] for e in data["mutations"].get("entity_upserts", [])]
    assert "concept:apex_global_delivery_model" in retained_ids
    assert not any("Pruned orphan entity node" in c and "apex_global_delivery_model" in c for c in changes)


def test_orphan_prune_removes_unconnected_unreferenced_entities_in_document_mode():
    """Adversarial check: Hallucinated entities with zero citations or mentions are still pruned."""
    data = {
        "meeting_archetype": "DOCUMENT_MODE",
        "evidence_index": {},
        "mutations": {
            "entity_upserts": [
                {
                    "id": "concept:hallucinated_unreferenced",
                    "name": "Hallucinated Unreferenced",
                    "evidence_ids": [],
                }
            ],
            "relationship_signals": [],
        },
    }
    changes: List[str] = []
    meta = {"is_document": True}

    heal_graph_projection(data, changes, metadata=meta, output_1="")

    retained_ids = [e["id"] for e in data["mutations"].get("entity_upserts", [])]
    assert "concept:hallucinated_unreferenced" not in retained_ids
    assert any("Pruned orphan entity node" in c for c in changes)


def test_narrative_timestamp_healer_skipped_for_documents():
    """Invariant preservation: Slide bracket references must never be rewritten to 'during the session'."""
    data = {
        "meeting_archetype": "DOCUMENT_MODE",
        "evidence_index": {},
        "mutations": {
            "decision_updates": [
                {
                    "id": "dec-01",
                    "narrative": "Approved the AMS transition framework [Slide 4: Governance] without objection.",
                }
            ],
            "entity_upserts": [],
            "relationship_signals": [],
        },
    }
    changes: List[str] = []
    meta = {"is_document": True}

    heal_graph_projection(data, changes, metadata=meta, output_1="")

    narrative = data["mutations"]["decision_updates"][0]["narrative"]
    assert "during the session" not in narrative
    assert "[Slide 4: Governance]" in narrative


def test_substantive_transcript_and_neet_healers_skipped_for_documents():
    """Boundary & malformed: Spoken dialogue turns and APEX_TRANS attendees must not be injected into document graphs."""
    data = {
        "meeting_id": "apex_trans-01",
        "title": "APEX_TRANS Solution Architecture Document",
        "is_document": True,
        "evidence_index": {},
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
        },
    }
    f1_speaker_text = "Alice (00:01): We should review the architecture.\nBob (00:05): Agreed, let's look at slide 2.\nCharlie (00:10): Looks good."
    changes: List[str] = []

    heal_projection_evidence_and_coverage(data, metadata={"is_document": True}, f1_text=f1_speaker_text, changes=changes)

    # Must NOT have injected dialogue turns or APEX_TRANS weather
    ev_idx = data.get("evidence_index", {})
    assert not any("dialogue" in str(k).lower() for k in ev_idx.keys())
    ent_ids = [e.get("id") for e in data["graph_mutations"].get("entity_upserts", [])]
    assert not any("dtn_weather" in str(eid).lower() for eid in ent_ids)


def test_duration_mutation_budget_scaling_for_documents():
    """Overflow & exhaustion: Mutation budget must scale with document units and text volume."""
    # 1. 25 slides deck (like AMS 101 Florida)
    deck_meta = {
        "is_document": True,
        "source_units": [{"slide": i} for i in range(25)],
    }
    deck_budget = resolve_duration_mutation_budget(deck_meta, text="word " * 3000)
    assert deck_budget >= 60

    # 2. Large document text (>= 15,000 characters)
    text_meta = {"is_document": True}
    large_text = "Apex Global Enterprises AMS Architecture Specification " * 400  # > 15,000 chars
    text_budget = resolve_duration_mutation_budget(text_meta, text=large_text)
    assert text_budget >= 60

    # 3. Small single-page document
    small_meta = {"is_document": True, "source_units": [{"slide": 1}]}
    small_budget = resolve_duration_mutation_budget(small_meta, text="Small text")
    assert 30 <= small_budget <= 45

    # 4. Caps in _heal_mutation_budget scale appropriately
    data = {
        "evidence_index": {},
        "mutations": {
            "strategic_intent_signals": [{"id": f"strat-{i}"} for i in range(15)],
            "proposal_updates": [{"id": f"prop-{i}"} for i in range(12)],
        },
    }
    changes = []
    _heal_mutation_budget(
        data,
        data["mutations"],
        changes,
        max_budget=30,
        is_doc=True,
        metadata=deck_meta,
        f1_text="word " * 3000,
    )
    # With budget=60 (scale=2.0), strat_cap = max(10, int(10*2)) = 20, proposals_cap = max(8, int(8*2)) = 16
    assert len(data["mutations"]["strategic_intent_signals"]) == 15  # None trimmed because 15 <= 20
    assert len(data["mutations"]["proposal_updates"]) == 12  # None trimmed because 12 <= 16


def test_c0_pa_runner_locks_document_mode(tmp_path):
    """Execution invariant: execute_c0_pa must unconditionally set DOCUMENT_MODE archetype for documents."""
    class DummyTracker:
        def start_stage(self, stage): pass
        def complete_stage(self, stage, status=None, details=None): pass

    meta = {
        "is_document": True,
        "meeting_name": "Deep Dive Technical Review of Architecture",  # contains keywords that would normally trigger ARCHITECTURE_DEEP_DIVE
    }
    out_p = tmp_path / "output"
    out_p.mkdir()

    pa_context, _ = execute_c0_pa(
        pipeline=None,
        modality=CanonicalModality.DOCUMENT,
        source_text="Document contents",
        input_hash="hash123",
        meta=meta,
        presentation_path=None,
        out_p=out_p,
        tracker=DummyTracker(),
    )

    assert meta["archetype"] == "DOCUMENT_MODE"
    assert meta["meeting_type"] == "DOCUMENT_MODE"
    assert pa_context["metadata"]["archetype"] == "DOCUMENT_MODE"


def test_wave1_hybrid_pdf_visual_region_locators_and_pure_text_fast_path():
    """Wave 1: Hybrid PDF pages (char_len >= 60 and image_count > 0) emit p{n}#img{i} locators while pure-text pages stay TEXT_ONLY."""
    from dual_pipeline.document_pipeline_core.visual_escalation import (
        VisualDisposition,
        assess_pdf_page,
        escalate_pdf_pages,
        validate_visual_routing_invariants,
    )

    pure_text = "Slide 4: The Business Opportunity — Enterprise Agentic Managed Services market growth and multi-year recurring revenue."
    pure_res = assess_pdf_page(4, pure_text, image_count=0)
    assert pure_res.disposition == VisualDisposition.TEXT_ONLY
    assert pure_res.flagged_locators == []
    assert pure_res.reason == "TEXT_NATIVE_CONFIRMED"

    hybrid_text = "Slide 8: Where We Are Winning — Initial deal value and follow-on expansion across four enterprise accounts."
    hybrid_res = assess_pdf_page(8, hybrid_text, image_count=4)
    assert hybrid_res.flagged_locators == ["p8#img1", "p8#img2", "p8#img3", "p8#img4"]
    assert hybrid_res.reason == "HYBRID_TEXT_WITH_EMBEDDED_IMAGES"

    escalated_batch = escalate_pdf_pages([(4, pure_text, 0), (8, hybrid_text, 4)], escalate_hybrid=True)
    assert escalated_batch[0].disposition == VisualDisposition.TEXT_ONLY
    assert escalated_batch[1].disposition == VisualDisposition.HYBRID_VISUAL_REQUIRED
    assert escalated_batch[1].flagged_locators == ["p8#img1", "p8#img2", "p8#img3", "p8#img4"]

    # Routing specific image region on hybrid page is allowed; routing pure-text page p4 fails closed
    validate_visual_routing_invariants(pages=[pure_res, hybrid_res], attempted_raster_targets=["p8#img1"])
    with pytest.raises(ValueError, match="pure-text PDF page 'p4'"):
        validate_visual_routing_invariants(pages=[pure_res, hybrid_res], attempted_raster_targets=["p4"])


def test_wave4_density_proportional_budget_and_compound_role_preservation():
    """Wave 4: 25-slide deck receives >= 100 mutation budget and compound ' & ' role titles are preserved intact."""
    from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection
    from dual_pipeline.transcript_stages_core.graph_post_helpers import _normalize_entities

    deck_meta = {
        "is_document": True,
        "source_units": [{"slide": i} for i in range(1, 26)],
    }
    deck_budget = resolve_duration_mutation_budget(deck_meta, text="## Slide 1\n- Bullet\n| A | B |\n" * 25)
    assert deck_budget >= 100

    entities = [
        {"id": "role:data-and-solution-lead", "type": "role", "name": "Data & Solution Lead", "evidence_ids": ["ev-24"]},
        {"id": "role:gtm-enablement-and-operations-lead", "type": "role", "name": "GTM Enablement & Operations Lead", "evidence_ids": ["ev-24"]},
    ]
    _normalize_entities(entities, [])
    assert entities[0]["name"] == "Data & Solution Lead"
    assert entities[1]["name"] == "GTM Enablement & Operations Lead"

    payload = {
        "evidence_index": {
            "ev-24": {
                "statement_id": "ev-24",
                "speaker": "Document Author",
                "source_classification": "FACT",
                "text": "Tauseefistiaq is Data & Solution Lead and Ali Imam is GTM Enablement & Operations Lead.",
                "exact_text": "Tauseefistiaq is Data & Solution Lead and Ali Imam is GTM Enablement & Operations Lead.",
                "locator": {"kind": "slide", "value": "Slide 24"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [dict(e) for e in entities],
            "relationship_signals": [],
        },
    }
    changes: List[str] = []
    heal_graph_projection(payload, changes, metadata=deck_meta, output_1="## Slide 24\nTauseefistiaq is Data & Solution Lead and Ali Imam is GTM Enablement & Operations Lead.")
    healed_names = {e["id"]: e["name"] for e in payload["graph_mutations"]["entity_upserts"]}
    assert healed_names.get("role:data-and-solution-lead") == "Data & Solution Lead"
    assert healed_names.get("role:gtm-enablement-and-operations-lead") == "GTM Enablement & Operations Lead"


def test_role_predicate_normalization_and_platform_entity_deduplication():
    """Verify HOLDS_ROLE maps to HAS_ROLE and cross-type duplicates (system vs org) canonicalize to system."""
    from dual_pipeline.transcript_qa_core.semantic_linters import _normalize_predicate
    from dual_pipeline.transcript_stages_core.graph_post_helpers import _resolve_relation_endpoints_and_pred
    from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection

    assert _normalize_predicate("HOLDS_ROLE") == "HAS_ROLE"
    assert _normalize_predicate("SERVES_AS_ROLE") == "HAS_ROLE"

    rel = {"from_id": "person:anthony", "to_id": "role:ams-lead"}
    f, t, p = _resolve_relation_endpoints_and_pred(rel, "HOLDS_ROLE")
    assert f == "person:anthony"
    assert t == "role:ams-lead"
    assert p == "HAS_ROLE"

    payload = {
        "meeting_archetype": "DOCUMENT_MODE",
        "evidence_index": {
            "ev-01": {"locator": {"kind": "slide", "value": "Slide 2"}, "exact_text": "Anthony Marquardt AMS Lead"},
            "ev-02": {"locator": {"kind": "slide", "value": "Slide 17"}, "exact_text": "Qurrent AI agents automate invoice processing"},
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev-01"]},
                {"id": "person:anthony", "type": "person", "name": "Anthony Marquardt", "evidence_ids": ["ev-01"]},
                {"id": "org:qurrent", "type": "org", "name": "Qurrent", "evidence_ids": ["ev-02"]},
                {"id": "system:qurrent", "type": "system", "name": "Qurrent", "evidence_ids": ["ev-02"]},
            ],
            "relationship_signals": [
                {"signal_id": "sig-01", "from_id": "person:anthony", "predicate": "MEMBER_OF", "to_id": "org:partner_advisory", "evidence_ids": ["ev-01"]},
                {"signal_id": "sig-02", "from_id": "org:partner_advisory", "predicate": "PARTNERS_WITH", "to_id": "org:qurrent", "evidence_ids": ["ev-02"]},
            ],
        },
    }
    changes = []
    meta = {"is_document": True, "project": "internal_consulting"}
    heal_graph_projection(payload, changes, metadata=meta, output_1="")

    ents = {e["id"]: e for e in payload["graph_mutations"]["entity_upserts"]}
    assert "system:qurrent" in ents
    assert "org:qurrent" not in ents
    assert "org:apex_global" not in ents
    assert "org:apex_trans" not in ents
    # Sig 02 should have its to_id remapped from org:qurrent to system:qurrent
    sig2 = next(s for s in payload["graph_mutations"]["relationship_signals"] if s["signal_id"] == "sig-02")
    assert sig2["to_id"] == "system:qurrent"



