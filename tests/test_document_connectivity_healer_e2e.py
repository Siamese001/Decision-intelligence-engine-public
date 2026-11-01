"""End-to-end tests for Wave 3: Deterministic Orphan Auto-Connector & Specialized Extractor Fixes."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    _compute_degrees,
    heal_disconnected_document_entities,
)
from dual_pipeline.document_pipeline_core.entity_canonicalizer import canonicalize_projection_entities
from dual_pipeline.document_pipeline_core.sow_contract_extractor import extract_offering_playbook_projection
from dual_pipeline.document_pipeline_core.workshop_extractor import extract_sparse_enablement_workshop_projection


def _assert_zero_orphans(proj: dict) -> None:
    gm = proj["graph_mutations"]
    upserts = gm["entity_upserts"]
    rels = gm["relationship_signals"]
    deg, _ = _compute_degrees(upserts, rels)
    orphans = [eid for eid, d in deg.items() if d == 0]
    assert orphans == [], f"Found orphan entities: {orphans}"


def test_sales_immersion_day4_connects_stephen_weinrich() -> None:
    day4_text = (
        "## Page 10\nPartnerAdvisory Sales Immersion Establishing & Expanding C-suite Relationships\n"
        "## Page 15\nFaven McShane Senior Sales Executive\n"
        "## Page 58\nUnified AI Transformation GTM Kit\n"
        "## Page 60\nSolution Catalog Application Modernization Stephen Weinrich Will Bray Amalia Goodwin Tamarah Usher\n"
    )
    proj = extract_sparse_enablement_workshop_projection(day4_text, {})
    _assert_zero_orphans(proj)
    rels = proj["graph_mutations"]["relationship_signals"]
    assert any(
        r["from_id"] == "person:stephen-weinrich"
        and r["predicate"] == "OWNS"
        and r["to_id"] == "system:application-modernization"
        for r in rels
    )


def test_ams_101_playbook_connects_partner_advisory_and_leadership_to_ams() -> None:
    ams_text = (
        "## Page 2\nAnthony Marquardt — AMS Lead\nBecca Glosser — GTM Enablement\n"
        "## Page 5\nAGENTIC MANAGED SERVICES human oversight.\n"
        "## Page 8\nOutsource functions (PMO + change) led by humans, supported by agents on our PartnerAdvisory Traverse platform.\n"
        "## Page 23\nMinimum run deal size: $1M+ multi-year project engagement instead.\n"
    )
    proj = extract_offering_playbook_projection(ams_text, {})
    _assert_zero_orphans(proj)
    rels = proj["graph_mutations"]["relationship_signals"]
    assert any(
        r["from_id"] == "org:partner_advisory" and r["to_id"] == "workstream:agentic-managed-services"
        for r in rels
    )


def test_connectivity_healer_eliminates_orphans_across_real_projections() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    sample_paths = [
        repo_root / "output/nea/10-21-2026/Forge_Ai_Optimization_Engine_Lifecycle_O/03_Forge_Ai_Optimization_Engine_Lifecycle_O_10-21-2026_0144PM_ET_canonical_signal_projection.json",
        repo_root / "output/project_nexus/09-30-2026/Dociq_Update_Jack_PartnerAdvisory/03_Dociq_Update_Jack_PartnerAdvisory_09-30-2026_canonical_signal_projection.json",
        repo_root / "output/market_intelligence/10-20-2026/meridian_utility_Distribution_Doug/03_meridian_utility_Distribution_Doug_10-20-2026_0334AM_ET_canonical_signal_projection.json",
        repo_root / "output/nea/10-12-2026/Cweos_Q4_Mission_Documentation/03_Cweos_Q4_Mission_Documentation_10-12-2026_0643PM_ET_canonical_signal_projection.json",
        repo_root / "output/hrit/10-24-2026/Hr360_Position_Request_Form_Clement/03_Hr360_Position_Request_Form_Clement_10-24-2026_0616AM_ET_canonical_signal_projection.json",
        repo_root / "output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Amit/03_Staffing_Transitions_Delivery_Amit_10-22-2026_0536PM_ET_canonical_signal_projection.json",
    ]
    for p in sample_paths:
        raw = json.loads(p.read_text(encoding="utf-8"))
        healed = canonicalize_projection_entities(copy.deepcopy(raw))
        changes = heal_disconnected_document_entities(healed, {"slug": p.parent.name})
        assert len(changes) >= 1
        _assert_zero_orphans(healed)
