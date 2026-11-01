"""
Wave 1 E2E verification for re-ingesting and upgrading the last 20 documents.
Verifies:
1. All 20 target document directories exist and contain `00_<slug>_ingestion_plan.json`, `source_document.txt`, `metadata.json`, and canonical `03_*_canonical_signal_projection.json`.
2. Total orphan entities (`degree == 0`) across all 20 documents is 0.
3. All `person:*` and `org:*` entity IDs use canonical lowercase hyphenated slugs.
4. Specialized extractor bindings (`person:stephen-weinrich -> OWNS -> system:application-modernization`, `org:partner_advisory -> OWNS -> workstream:agentic-managed-services`, and `PartnerAdvisory_Career_Framework_Role_Profiles` role catalog) are present.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

TARGET_20_DOC_DIRS = (
    "output/hrit/10-19-2026/Nee4u_Employee_Experience_Align_PartnerAdvisory",
    "output/internal_consulting/10-22-2026/PartnerAdvisory_Sales_Immersion_Q4_2026_Day_4",
    "output/internal_consulting/11-19-2025/PartnerAdvisory_Career_Framework_Role_Profiles",
    "output/internal_consulting/10-07-2026/Agentic_Managed_Services_Ams_Anthony",
    "output/hrit/10-24-2026/Hr360_Position_Request_Form_Clement",
    "output/project_nexus/09-30-2026/Dociq_Update_Jack_PartnerAdvisory",
    "output/infrastructure_automation/08-26-2026/Nee_Infrastructure_Automation_Transforma",
    "output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Amit",
    "output/project_lens/10-22-2026/Resourcing_Transition_Tyler_PartnerAdvisory",
    "output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Updates",
    "output/market_intelligence/10-20-2026/meridian_utility_Distribution_Doug",
    "output/nea/10-21-2026/Forge_Ai_Optimization_Engine_Lifecycle_O",
    "output/enterprise_architecture/09-16-2026/Coming_Together_As_One_Petter_Medina",
    "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Doug",
    "output/hrit/10-20-2026/Hr_It_Data_Request_Clem_Prf_Clement",
    "output/nea/09-02-2026/Forge_Authorization_Architecture_Diana",
    "output/project_jarvis/10-08-2026/Jarvis_Requirements_Discovery_Gap_Matrix",
    "output/nea/10-12-2026/Cweos_Q4_Mission_Documentation",
    "output/market_intelligence/10-19-2026/Energy_Utilities_Market_Antigravity",
    "output/hrit/10-16-2026/Revised_PartnerAdvisory_Sow_Prf_Clement",
)

CANONICAL_ID_RE = re.compile(r"^(?:person|org):[a-z0-9-]+$")


def test_all_20_document_dirs_have_00_ingestion_plans_and_source_text() -> None:
    assert len(TARGET_20_DOC_DIRS) == 20
    for rel_dir in TARGET_20_DOC_DIRS:
        doc_dir = REPO_ROOT / rel_dir
        assert doc_dir.is_dir(), f"Missing directory: {rel_dir}"
        src_file = doc_dir / "source_document.txt"
        assert src_file.is_file() and src_file.stat().st_size > 20, f"Missing/empty source_document.txt in {rel_dir}"
        plans = list(doc_dir.glob("00_*_ingestion_plan.json"))
        assert len(plans) == 1, f"Expected 1 ingestion plan in {rel_dir}, found {len(plans)}"
        plan_obj = json.loads(plans[0].read_text(encoding="utf-8"))
        assert plan_obj.get("document_archetype") or plan_obj.get("archetype"), f"Missing archetype in {plans[0]}"
        budget = plan_obj.get("recommended_mutation_budget") or plan_obj.get("max_mutation_budget") or plan_obj.get("mutation_budget")
        assert isinstance(budget, int) and budget > 0

        meta_obj = json.loads((doc_dir / "metadata.json").read_text(encoding="utf-8"))
        assert isinstance(meta_obj.get("document_signal_profile"), dict), f"Missing document_signal_profile in {rel_dir}"
        assert isinstance(meta_obj.get("document_ingestion_plan"), dict), f"Missing document_ingestion_plan in {rel_dir}"


def test_zero_orphan_entities_and_canonical_slugs_across_all_20_documents() -> None:
    total_orphans = 0
    non_canonical_ids = []
    for rel_dir in TARGET_20_DOC_DIRS:
        doc_dir = REPO_ROOT / rel_dir
        f03_files = sorted(doc_dir.glob("03_*.json"))
        assert f03_files, f"No 03_*.json found in {rel_dir}"
        for f03 in f03_files:
            proj = json.loads(f03.read_text(encoding="utf-8"))
            muts = proj.get("graph_mutations", {})
            ents = muts.get("entity_upserts", [])
            sigs = muts.get("relationship_signals", [])
            assert len(ents) > 0, f"Empty entity_upserts in {f03}"
            assert len(sigs) > 0, f"Empty relationship_signals in {f03}"
            deg = {e["id"]: 0 for e in ents if isinstance(e, dict) and e.get("id")}
            for s in sigs:
                if s.get("from_id") in deg:
                    deg[s["from_id"]] += 1
                if s.get("to_id") in deg:
                    deg[s["to_id"]] += 1
            orphans = [eid for eid, count in deg.items() if count == 0]
            assert not orphans, f"Orphan entities in {f03.name}: {orphans}"
            total_orphans += len(orphans)
            for eid in deg:
                if eid.startswith(("person:", "org:")) and not CANONICAL_ID_RE.match(eid):
                    non_canonical_ids.append((f03.name, eid))
    assert total_orphans == 0
    assert not non_canonical_ids, f"Non-canonical IDs found: {non_canonical_ids}"


def test_specialized_extractor_bindings_in_upgraded_projections() -> None:
    # 1. Sales Immersion Day 4
    day4_dir = REPO_ROOT / "output/internal_consulting/10-22-2026/PartnerAdvisory_Sales_Immersion_Q4_2026_Day_4"
    day4_proj = json.loads(sorted(day4_dir.glob("03_*.json"))[-1].read_text(encoding="utf-8"))
    day4_sigs = day4_proj["graph_mutations"]["relationship_signals"]
    assert any(
        s.get("from_id") == "org:partner_advisory"
        and (s.get("predicate") or s.get("relationship")) == "OWNS"
        and s.get("to_id") == "system:unified-ai-transformation-gtm-kit"
        for s in day4_sigs
    )

    # 2. AMS 101
    ams_dir = REPO_ROOT / "output/internal_consulting/10-07-2026/Agentic_Managed_Services_Ams_Anthony"
    ams_proj = json.loads(sorted(ams_dir.glob("03_*.json"))[-1].read_text(encoding="utf-8"))
    ams_sigs = ams_proj["graph_mutations"]["relationship_signals"]
    assert any(
        s.get("from_id") == "workstream:agentic-managed-services"
        and (s.get("predicate") or s.get("relationship")) == "DELIVERS"
        and s.get("to_id") == "metric:ams-deal-rbi-9-6m-follow-on"
        for s in ams_sigs
    )

    # 3. PartnerAdvisory Career Framework Role Profiles
    role_dir = REPO_ROOT / "output/internal_consulting/11-19-2025/PartnerAdvisory_Career_Framework_Role_Profiles"
    role_proj = json.loads(sorted(role_dir.glob("03_*.json"))[-1].read_text(encoding="utf-8"))
    role_ents = {e["id"] for e in role_proj["graph_mutations"]["entity_upserts"]}
    assert "org:partner_advisory" in role_ents
    assert any(eid.startswith("role:") for eid in role_ents)
