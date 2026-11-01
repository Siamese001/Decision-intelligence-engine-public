"""
tests/test_worktree_naming.py

Unit and regression test suite for DocIQ taxonomy registration, anti-conflation,
and intelligent worktree and branch derivation.
"""
from __future__ import annotations

import pytest

from dual_pipeline.in_memory_graph import _infer_assertion_scope
from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    _PROJECT_KEYWORD_MAP,
)
from dual_pipeline.parameters import (
    get_domain_for_project,
    get_known_projects,
    get_project_domains,
    infer_project_from_title,
    resolve_project_name,
)
from dual_pipeline.transcript_stages_core.anti_conflation import (
    is_compound_workstream_reference,
)


def test_dociq_is_registered_in_known_projects():
    """Verify dociq is a first-class project identifier in the registry."""
    known = get_known_projects()
    assert "dociq" in known
    assert "project_jarvis" in known
    assert "project_nexus" in known


def test_dociq_and_hierarchical_aliases_resolve():
    """Verify colloquial and hierarchical domain aliases resolve to canonical IDs."""
    assert resolve_project_name("dociq") == "dociq"
    assert resolve_project_name("DOCIQ") == "dociq"
    assert resolve_project_name("project dociq") == "dociq"
    assert resolve_project_name("project_dociq") == "dociq"
    assert resolve_project_name("nea dociq") == "dociq"
    assert resolve_project_name("pgd dociq") == "dociq"
    assert resolve_project_name("nea_dociq") == "dociq"
    assert resolve_project_name("nea_project_lens") == "project_lens"
    assert resolve_project_name("edo_project_spring") == "project_spring"


def test_7_domain_hierarchical_taxonomy_mapping():
    """Verify the 7 canonical domains and workstream domain resolution."""
    domains = get_project_domains()
    assert set(domains.keys()) == {
        "NEA",
        "EDO",
        "APEX_TRANS",
        "NEE_IT",
        "ENTERPRISE_OPS",
        "INTERNAL_CONSULTING",
        "MARKET_INTELLIGENCE",
    }
    assert get_domain_for_project("dociq") == "NEA"
    assert get_domain_for_project("project_lens") == "NEA"
    assert get_domain_for_project("project_jarvis") == "NEA"
    assert get_domain_for_project("project_nexus") == "NEA"
    assert get_domain_for_project("project_spring") == "EDO"
    assert get_domain_for_project("edo") == "EDO"
    assert get_domain_for_project("apex_trans") == "APEX_TRANS"
    assert get_domain_for_project("project_beacon") == "NEE_IT"
    assert get_domain_for_project("project_atlas") == "ENTERPRISE_OPS"
    assert get_domain_for_project("enterprise_account_planning") == "INTERNAL_CONSULTING"
    assert get_domain_for_project("partner_alliances") == "INTERNAL_CONSULTING"
    assert get_domain_for_project("market_intelligence") == "MARKET_INTELLIGENCE"


def test_infer_project_from_title_priority():
    """Verify priority-ordered title inference for dociq, jarvis, and nexus."""
    assert infer_project_from_title("Dociq_Q4_Scope_Bailey_Amit") == "dociq"
    assert infer_project_from_title("09-30-2026 Dociq Q4 Scope") == "dociq"
    assert infer_project_from_title("Collateral_Dociq_Q4_Plan_Charter_Scope") == "dociq"
    assert infer_project_from_title("Subject_Dociq_Q4_Scope_Executive_Strategy") == "dociq"
    assert infer_project_from_title("Project Nexus & DocIQ Alignment") == "dociq"
    assert infer_project_from_title("Nexus Architecture Sprint") == "project_nexus"
    assert infer_project_from_title("Jarvis Standup") == "project_jarvis"


def test_in_memory_graph_scope_disentanglement():
    """Verify in_memory_graph._infer_assertion_scope isolates dociq and jarvis."""
    assertion_dociq = {
        "primary_source_id": "s1",
        "scope_text": "dociq extraction accuracy",
        "subject_node_id": "dociq_pipeline",
        "object_node_id": "metric_accuracy",
        "object_literal": "99% precision",
    }
    assert _infer_assertion_scope(assertion_dociq, {}) == "project:dociq"

    assertion_jarvis = {
        "primary_source_id": "s2",
        "scope_text": "jarvis developer workflow",
        "subject_node_id": "jarvis_copilot",
        "object_node_id": "metric_velocity",
        "object_literal": "2x throughput",
    }
    assert _infer_assertion_scope(assertion_jarvis, {}) == "project:project_jarvis"

    assertion_nexus = {
        "primary_source_id": "s3",
        "scope_text": "nexus platform infrastructure",
        "subject_node_id": "nexus_core",
        "object_node_id": "metric_uptime",
        "object_literal": "99.9%",
    }
    assert _infer_assertion_scope(assertion_nexus, {}) == "project:project_nexus"


def test_copilot_connection_policy_keyword_map():
    """Verify _PROJECT_KEYWORD_MAP has separate entries for dociq, jarvis, nexus."""
    kw_to_proj = {kw[0]: proj for kw, proj in _PROJECT_KEYWORD_MAP}
    assert kw_to_proj.get("dociq") == "project:dociq"
    assert kw_to_proj.get("jarvis") == "project:project_jarvis"
    assert kw_to_proj.get("nexus") == "project:project_nexus"


def test_anti_conflation_disentangles_nexus_and_dociq():
    """Verify anti-conflation detects compound workstream conflation with nexus."""
    assert is_compound_workstream_reference("project nexus / dociq") is True
    assert is_compound_workstream_reference("nexus_dociq") is True
    assert is_compound_workstream_reference("nexus_jarvis") is True
    assert is_compound_workstream_reference("dociq_lens") is True
    assert is_compound_workstream_reference("dociq") is False
    assert is_compound_workstream_reference("project_nexus") is False


def test_derive_worktree_identity_dociq_override():
    """Verify derive_worktree_identity overrides generic project_nexus with dociq from title."""
    from dual_pipeline.canonical_pipeline.worktree_naming import derive_worktree_identity

    wt_dir, branch = derive_worktree_identity(
        modality="MEETING_TRANSCRIPT",
        title="Dociq_Q4_Scope_Bailey_Amit",
        project="project_nexus",
        date="09-30-2026",
        clean_slug="Dociq_Q4_Scope_Bailey_Amit",
    )
    assert "dociq" in branch
    assert "project-nexus" not in branch
    assert "project_nexus" not in str(wt_dir)
    assert branch.startswith("transcript/dociq-09-30-2026")


def test_derive_worktree_identity_doc_dociq_no_nexus():
    """Verify document modality for DocIQ collateral does not contain project-nexus."""
    from dual_pipeline.canonical_pipeline.worktree_naming import derive_worktree_identity

    wt_dir, branch = derive_worktree_identity(
        modality="DOCUMENT",
        title="Collateral_Dociq_Q4_Plan_Charter_Scope",
        project="project_nexus",
        date="10-01-2026",
    )
    assert "dociq" in branch
    assert "project-nexus" not in branch
    assert "project_nexus" not in str(wt_dir)
    assert branch.startswith("ingest/doc-")


def test_resolve_project_name_prioritizes_workstream_and_title():
    """Verify lifecycle._resolve_project_name prioritizes explicit workstream and title."""
    from dual_pipeline.canonical_pipeline.lifecycle import _resolve_project_name

    meta_ws = {"workstream": "dociq", "project": "project_nexus"}
    assert _resolve_project_name(meta_ws, output_dir="auto") == "dociq"

    meta_title = {"meeting_name": "Dociq_Q4_Scope_Bailey_Amit", "project": "project_nexus"}
    assert _resolve_project_name(meta_title, output_dir="auto") == "dociq"

    meta_doc = {"title": "Collateral_Dociq_Q4_Plan_Charter_Scope", "project": "auto"}
    assert _resolve_project_name(meta_doc, output_dir="auto") == "dociq"


def test_determine_project_directory_dociq_resolution():
    """Verify determine_project_directory resolves dociq from metadata and title."""
    from dual_pipeline.query_router_core.project_router import determine_project_directory

    res = determine_project_directory({"title": "Collateral_Dociq_Q4_Plan_Charter_Scope", "project": "auto"})
    assert res == "dociq"

    res_ws = determine_project_directory({"workstream": "dociq", "project": "project_nexus"})
    assert res_ws == "dociq"


def test_parallel_ingestor_default_project_auto():
    """Verify ParallelDocumentIngestor defaults to project='auto'."""
    from dual_pipeline.document_pipeline_core.parallel_ingestor import ParallelDocumentIngestor

    ingestor = ParallelDocumentIngestor()
    assert ingestor.project == "auto"

