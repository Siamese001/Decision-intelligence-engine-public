"""
tests/test_project_taxonomy_adversarial.py

Adversarial and boundary tests for hierarchical project taxonomy SSOT.
Covers:
1. Boundary payload: null/empty projects, whitespace, non-existent projects
2. Malformed input: mixed case, hyphen/underscore injection, symbol corruption
3. Concurrency & race: parallel project resolution across multiple threads
4. Invariant bypass attempt: attempts to conflate DocIQ/Jarvis into Nexus or bypass domain isolation
5. Exact production regression: verifies Dociq_Q4_Scope_Bailey_Amit resolves to dociq under NEA
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
import pytest

from dual_pipeline.parameters import (
    get_domain_for_project,
    get_known_projects,
    get_project_aliases,
    infer_project_from_title,
    resolve_project_name,
)
from dual_pipeline.in_memory_graph import _infer_assertion_scope


def test_adversarial_boundary_payload_nulls_and_empty():
    """Boundary: Handles empty, whitespace, and unknown project names cleanly."""
    assert resolve_project_name("") == "project_lens"
    assert resolve_project_name("   ") == "project_lens"
    assert get_domain_for_project("") is None
    assert get_domain_for_project(None) is None
    assert get_domain_for_project("non_existent_project_xyz") is None


def test_adversarial_malformed_input_casing_and_punctuation():
    """Malformed input: Casing, punctuation, and noisy inputs resolve correctly."""
    assert resolve_project_name("  NEA-DOCIQ  ") == "dociq"
    assert resolve_project_name("Project_Jarvis") == "project_jarvis"
    assert resolve_project_name("EDO-PROJECT-SPRING") == "project_spring"
    assert get_domain_for_project("  NEA-DOCIQ  ") == "NEA"
    assert get_domain_for_project("edo-project-spring") == "EDO"
    assert get_domain_for_project("Internal-PartnerAdvisory-Florida-Account-Planning") == "INTERNAL_CONSULTING"


def test_adversarial_concurrency_parallel_domain_resolution():
    """Concurrency & race: 32 parallel threads resolving domains and projects concurrently."""
    queries = [
        ("dociq", "NEA"),
        ("project_spring", "EDO"),
        ("apex_trans", "APEX_TRANS"),
        ("project_beacon", "NEE_IT"),
        ("project_atlas", "ENTERPRISE_OPS"),
        ("enterprise_account_planning", "INTERNAL_CONSULTING"),
        ("market_intelligence", "MARKET_INTELLIGENCE"),
    ] * 8

    def _resolve(q):
        proj, expected_domain = q
        resolved_proj = resolve_project_name(proj)
        domain = get_domain_for_project(resolved_proj)
        return domain == expected_domain

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_resolve, queries))

    assert len(results) == 56
    assert all(results)


def test_adversarial_invariant_bypass_anti_conflation():
    """Invariant bypass attempt: ensure dociq, jarvis, and nexus never conflate or bleed scopes."""
    assert resolve_project_name("dociq") == "dociq"
    assert resolve_project_name("jarvis") == "project_jarvis"
    assert resolve_project_name("nexus") == "project_nexus"

    # in_memory_graph assertion scope must keep them distinct
    assert _infer_assertion_scope({"scope_text": "dociq feature delivery"}) == "project:dociq"
    assert _infer_assertion_scope({"scope_text": "jarvis feature delivery"}) == "project:project_jarvis"
    assert _infer_assertion_scope({"scope_text": "nexus feature delivery"}) == "project:project_nexus"

    # None of them should collapse into the others
    assert _infer_assertion_scope({"scope_text": "dociq feature delivery"}) != "project:project_nexus"
    assert _infer_assertion_scope({"scope_text": "jarvis feature delivery"}) != "project:project_nexus"


def test_adversarial_exact_production_regression_dociq_q4():
    """Exact production regression: Dociq_Q4_Scope_Bailey_Amit resolves to dociq in domain NEA."""
    title = "Dociq_Q4_Scope_Bailey_Amit"
    inferred = infer_project_from_title(title)
    assert inferred == "dociq"
    assert get_domain_for_project(inferred) == "NEA"
