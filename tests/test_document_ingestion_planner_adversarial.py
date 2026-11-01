"""
Adversarial and edge-case tests for the Pre-Execution Document Ingestion Planner
and PartnerAdvisory Career Framework Role Profiles catalog extractor.
Covers:
1. Boundary payload (empty input, whitespace-only input, zero-page input)
2. Malformed input (corrupted metadata types, missing page markers, non-ASCII/special slug characters)
3. Overflow & noise filtering (4-tier role milestone bloat, Salesforce supplement, low-signal roles)
4. Idempotent re-ingestion & deterministic plan digest stability
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.document_pipeline_core.executor import _build_document_user_content
from dual_pipeline.document_pipeline_core.ingestion_planner import (
    build_document_ingestion_plan,
    extract_role_framework_catalog_projection,
    persist_document_ingestion_plan,
)
from dual_pipeline.document_pipeline_core.quality import (
    validate_slide_deck_coverage_and_yield,
)


def test_boundary_empty_and_whitespace_payloads(tmp_path: Path) -> None:
    empty_plan = build_document_ingestion_plan("", None)
    assert empty_plan["planned_before_execution"] is True
    assert empty_plan["total_pages_detected"] == 0
    assert empty_plan["selected_high_signal_count"] == 0

    ws_plan = build_document_ingestion_plan("   \n\t  ", {})
    assert ws_plan["total_pages_detected"] == 0
    path = persist_document_ingestion_plan(ws_plan, tmp_path, slug="03_///weird slug***")
    assert path.exists()
    assert path.name.startswith("00_")


def test_malformed_metadata_and_missing_page_headers() -> None:
    raw_text = (
        "me@partner_advisory: Career Framework Role Profiles\n"
        "Understanding Role Profile Components and Relationship to Other Roles.\n"
        "Solution Owner leads Agile Software Delivery under Product Engineering Methodology (PEM).\n"
        "Project Manager owns Schedule Management and Scope Management.\n"
    )
    plan = build_document_ingestion_plan(raw_text, {"document_archetype": "INVALID_ARCHETYPE_TOKEN"})
    assert plan["document_archetype"] == "ROLE_FRAMEWORK_CATALOG"
    assert plan["total_pages_detected"] == 1
    assert plan["selected_high_signal_count"] == 2

    proj = extract_role_framework_catalog_projection(raw_text, {})
    ent_ids = {e["id"] for e in proj["graph_mutations"]["entity_upserts"]}
    assert "role:solution-owner" in ent_ids
    assert "role:project-manager" in ent_ids
    for ev in proj["evidence_index"].values():
        assert ev["locator"]["value"] == "p1"


def test_overflow_milestone_bloat_and_excluded_role_quarantine() -> None:
    noisy_catalog = "\n".join([
        "## Page 1",
        "me@partner_advisory: Career Framework Role Profiles",
        "Understanding Role Profile Components and Role Milestones.",
        "## Page 9",
        "AI Coach guides Responsible AI adoption.",
        "## Page 10",
        "Role Milestones: Consultant / Senior Consultant / Principal / Director 4-tier HR rubric.",
        "## Page 104",
        "Visual Designer creates visual brand assets.",
        "## Page 108",
        "Customer Strategist designs customer journeys.",
        "## Page 112",
        "Talent Strategist designs talent programs.",
        "## Page 197",
        "Solution Owner leads agile pods using Product Engineering Methodology (PEM).",
        "## Page 234",
        "Engagement Lead oversees day-to-day engagement operations.",
        "## Page 242",
        "Salesforce Supplement: Salesforce Technical Lead and Functional Lead.",
    ])
    plan = build_document_ingestion_plan(noisy_catalog, {})
    proj = extract_role_framework_catalog_projection(noisy_catalog, {"document_ingestion_plan": plan})
    ent_ids = {e["id"] for e in proj["graph_mutations"]["entity_upserts"]}

    assert "role:ai-coach" in ent_ids
    assert "role:solution-owner" in ent_ids
    assert "role:engagement-lead" in ent_ids
    assert "role:visual-designer" not in ent_ids
    assert "role:customer-strategist" not in ent_ids
    assert "role:talent-strategist" not in ent_ids

    meta = {
        "document_archetype": "ROLE_FRAMEWORK_CATALOG",
        "document_ingestion_plan": plan,
    }
    mut_count = len(proj["graph_mutations"]["entity_upserts"]) + len(proj["graph_mutations"]["relationship_signals"])
    passed, errors, telemetry = validate_slide_deck_coverage_and_yield(
        proj, noisy_catalog, metadata=meta, mutation_count=mut_count
    )
    assert passed is True, f"Expected slide coverage pass on planned role pages, got errors: {errors}"
    assert telemetry["substantive_coverage_ratio"] == 1.0


def test_idempotent_plan_generation_and_prompt_injection() -> None:
    text = (
        "## Page 1\nme@partner_advisory: Career Framework Role Profiles\n"
        "Relationship to Other Roles and Role Milestones.\n"
        "## Page 197\nSolution Owner leads delivery pods.\n"
    )
    plan1 = build_document_ingestion_plan(text, {})
    plan2 = build_document_ingestion_plan(text, {})
    assert plan1["plan_id"] == plan2["plan_id"]
    assert plan1["source_hash"] == plan2["source_hash"]
    assert plan1["high_signal_targets"] == plan2["high_signal_targets"]

    user_content = _build_document_user_content(text, {"document_ingestion_plan": plan1})
    assert "<document_ingestion_plan>" in user_content
    parsed_block = user_content.split("<document_ingestion_plan>")[1].split("</document_ingestion_plan>")[0].strip()
    assert json.loads(parsed_block)["plan_id"] == plan1["plan_id"]
