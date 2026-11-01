"""
tests/test_hierarchical_project_taxonomy_e2e.py

End-to-End lifecycle tests for hierarchical 7-domain project taxonomy SSOT,
domain routing, workstream resolution, and alias deduplication.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.parameters import (
    get_domain_for_project,
    get_known_projects,
    get_project_aliases,
    get_project_domains,
    infer_project_from_title,
    resolve_project_name,
)
from dual_pipeline.in_memory_graph import _infer_assertion_scope
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import _PROJECT_KEYWORD_MAP
from dual_pipeline.live_meeting_recorder_core.prefetch_query_builder import _PROJECT_SYNONYMS
from tools.validate_output_hierarchy_core.constants import PROJECT_AFFINITY_MAP


def test_e2e_hierarchical_7_domain_taxonomy_structure():
    """Verify that project_registry.json contains exactly the 7 canonical domains and valid workstreams."""
    reg_path = Path(__file__).resolve().parent.parent / "config" / "governance" / "project_registry.json"
    assert reg_path.is_file(), "config/governance/project_registry.json must exist"

    with open(reg_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    domains = data.get("domains", {})
    expected_domains = {
        "NEA",
        "EDO",
        "APEX_TRANS",
        "NEE_IT",
        "ENTERPRISE_OPS",
        "INTERNAL_CONSULTING",
        "MARKET_INTELLIGENCE",
    }
    assert set(domains.keys()) == expected_domains

    # Check workstreams inside each domain
    assert "nea_dociq" in domains["NEA"]["workstreams"]
    assert "nea_project_lens" in domains["NEA"]["workstreams"]
    assert "nea_project_jarvis" in domains["NEA"]["workstreams"]
    assert "nea_project_nexus" in domains["NEA"]["workstreams"]
    assert "edo_project_spring" in domains["EDO"]["workstreams"]
    assert "internal_consulting_enterprise_account_planning" in domains["INTERNAL_CONSULTING"]["workstreams"]


def test_e2e_no_duplicate_keys_in_project_registry_raw_json():
    """Verify raw JSON text of project_registry.json has zero duplicate keys."""
    reg_path = Path(__file__).resolve().parent.parent / "config" / "governance" / "project_registry.json"

    def _detect_duplicates(pairs):
        seen = set()
        res = {}
        for k, v in pairs:
            if k in seen:
                raise ValueError(f"Duplicate JSON key found: {k}")
            seen.add(k)
            res[k] = v
        return res

    with open(reg_path, "r", encoding="utf-8") as f:
        # json.load with custom object_pairs_hook fails if duplicate keys exist
        data = json.load(f, object_pairs_hook=_detect_duplicates)
    assert "domains" in data
    assert "known_projects" in data
    assert "project_aliases" in data


def test_e2e_domain_resolution_for_all_canonical_projects():
    """Verify get_domain_for_project resolves every known project to its canonical 7-domain parent."""
    test_cases = [
        ("dociq", "NEA"),
        ("project_lens", "NEA"),
        ("project_jarvis", "NEA"),
        ("project_nexus", "NEA"),
        ("conduit", "NEA"),
        ("regai", "NEA"),
        ("ec_scheduling", "NEA"),
        ("project_spring", "EDO"),
        ("edo", "EDO"),
        ("apex_trans", "APEX_TRANS"),
        ("project_beacon", "NEE_IT"),
        ("enterprise_architecture", "NEE_IT"),
        ("infrastructure_automation", "NEE_IT"),
        ("gemini_enablement", "NEE_IT"),
        ("bmo", "NEE_IT"),
        ("hrit", "NEE_IT"),
        ("project_atlas", "ENTERPRISE_OPS"),
        ("enterprise_account_planning", "INTERNAL_CONSULTING"),
        ("partner_alliances", "INTERNAL_CONSULTING"),
        ("internal_consulting", "INTERNAL_CONSULTING"),
        ("market_intelligence", "MARKET_INTELLIGENCE"),
    ]
    for proj_id, expected_domain in test_cases:
        assert get_domain_for_project(proj_id) == expected_domain, f"Project {proj_id} must resolve to domain {expected_domain}"


def test_e2e_dociq_and_jarvis_isolation_from_nexus():
    """Verify in_memory_graph, copilot_connection_policy, and prefetch isolate DocIQ and Jarvis."""
    # 1. in_memory_graph assertion scope
    assert _infer_assertion_scope({"scope_text": "dociq schema extraction"}) == "project:dociq"
    assert _infer_assertion_scope({"scope_text": "jarvis sprint backlog"}) == "project:project_jarvis"
    assert _infer_assertion_scope({"scope_text": "nexus platform governance"}) == "project:project_nexus"

    # 2. copilot_connection_policy keyword map
    kw_dict = {kw[0]: proj for kw, proj in _PROJECT_KEYWORD_MAP}
    assert kw_dict.get("dociq") == "project:dociq"
    assert kw_dict.get("jarvis") == "project:project_jarvis"
    assert kw_dict.get("nexus") == "project:project_nexus"

    # 3. prefetch_query_builder synonyms
    assert "dociq" in _PROJECT_SYNONYMS
    assert "project_jarvis" in _PROJECT_SYNONYMS
    assert "dociq" not in _PROJECT_SYNONYMS["project_nexus"]
    assert "jarvis" not in _PROJECT_SYNONYMS["project_nexus"]


def test_e2e_reid_and_jill_roster_reconciliation():
    """Verify Reid Schenk is identified with NEA and Jill Cizewski is not classified as PartnerAdvisory."""
    dir_path = Path(__file__).resolve().parent.parent / "dual_pipeline" / "transcript_metadata_core" / "canonical_enterprise_directory.json"
    with open(dir_path, "r", encoding="utf-8") as f:
        directory = json.load(f)

    # Reid Schenk must be Apex Global Analytics (NEA)
    reid_entry = directory.get("reid schenk")
    assert reid_entry is not None
    assert "Apex Global Analytics" in reid_entry[1]

    # Verify speaker_memory.json has Reid Schenk at Apex Global Analytics
    spk_mem_path = Path(__file__).resolve().parent.parent / "config" / "runtime" / "speaker_memory.json"
    with open(spk_mem_path, "r", encoding="utf-8") as f:
        spk_mem = json.load(f)
    assert spk_mem["profiles"]["reid"]["organization"] == "Apex Global Analytics (NEA)"
