"""
tests/test_worktree_project_naming_adversarial.py

Adversarial and boundary edge-case tests for intelligent worktree naming and project resolution.
Covers at least 4 required categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: empty titles, None values, whitespace, missing project/date
2. Malformed input: symbol injection, mixed casing, punctuation-laden workstream names
3. Concurrency & race: parallel worktree identity derivation across concurrent threads
4. Invariant bypass attempt: attempts to force container project ('project_nexus') on DocIQ artifacts
5. Exact production regression: Dociq_Q4_Scope_Bailey_Amit and Subject_Dociq_Q4_Scope_Executive_Strategy
"""
from __future__ import annotations

import concurrent.futures
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.lifecycle import _resolve_project_name
from dual_pipeline.canonical_pipeline.worktree_naming import (
    CONVENTIONAL_BRANCH_RE,
    derive_worktree_identity,
)
from dual_pipeline.parameters import infer_project_from_title, resolve_project_name
from dual_pipeline.query_router_core.project_router import determine_project_directory
from tools.validate_output_hierarchy_core.helpers import _validate_project_affinity


def test_adversarial_category_1_boundary_payload_empty_and_nulls():
    """Category 1: Boundary payload - handles None, empty strings, and missing fields."""
    assert infer_project_from_title("") is None
    assert infer_project_from_title(None) is None
    assert resolve_project_name("") == "project_lens"

    wt_dir, branch = derive_worktree_identity(
        modality="MEETING_TRANSCRIPT",
        title="",
        project=None,
        date=None,
        clean_slug="",
    )
    assert CONVENTIONAL_BRANCH_RE.match(branch)
    assert branch == "transcript/session"
    assert wt_dir.name == "transcript_session"


def test_adversarial_category_2_malformed_input_symbols_and_casing():
    """Category 2: Malformed input - symbol injection, excessive delimiters, and mixed case."""
    malformed_title = "### [DOCIQ] !!! $100M Architecture Scope @ Bailey & Amit (2026) ###"
    inferred = infer_project_from_title(malformed_title)
    assert inferred == "dociq"

    wt_dir, branch = derive_worktree_identity(
        modality="MEETING_TRANSCRIPT",
        title=malformed_title,
        project="PROJECT_NEXUS",
        date="09/30/2026",
    )
    assert CONVENTIONAL_BRANCH_RE.match(branch)
    assert "dociq" in branch
    assert "project-nexus" not in branch
    assert "$" not in branch
    assert "@" not in branch
    assert "#" not in branch


def test_adversarial_category_3_concurrency_parallel_derivation():
    """Category 3: Concurrency & race - 16 parallel threads deriving worktree identities simultaneously."""
    test_cases = [
        ("MEETING_TRANSCRIPT", "Dociq_Q4_Scope_Bailey_Amit", "project_nexus", "09-30-2026"),
        ("DOCUMENT", "Collateral_Dociq_Q4_Plan_Charter_Scope", "auto", "10-01-2026"),
        ("MEETING_TRANSCRIPT", "Project Nexus Draft Charters", "project_nexus", "09-14-2026"),
        ("MEETING_TRANSCRIPT", "JARVIS Standup", "nea", "10-09-2026"),
    ] * 8  # 32 concurrent requests

    def _worker(case):
        mod, title, proj, dt = case
        return derive_worktree_identity(mod, title, project=proj, date=dt)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_worker, test_cases))

    assert len(results) == 32
    for wt_dir, branch in results:
        assert CONVENTIONAL_BRANCH_RE.match(branch)
        if "dociq" in branch:
            assert "project-nexus" not in branch


def test_adversarial_category_4_invariant_bypass_container_smuggling():
    """Category 4: Invariant bypass attempt - caller specifies container 'project_nexus' or 'nea' for DocIQ."""
    # Attempt 1: In lifecycle._resolve_project_name
    meta_bypass = {
        "title": "Dociq_Q4_Scope_Bailey_Amit",
        "project": "project_nexus",
    }
    resolved = _resolve_project_name(meta_bypass, output_dir="auto")
    assert resolved == "dociq", "DocIQ must not be smuggled into project_nexus"

    # Attempt 2: In determine_project_directory
    det = determine_project_directory({"title": "Dociq_Q4_Scope_Bailey_Amit", "project": "project_nexus"})
    assert det == "dociq", "Router must not allow container project to override explicit DocIQ title"

    # Attempt 3: In output hierarchy affinity check
    err = _validate_project_affinity("Dociq_Q4_Scope_Bailey_Amit", "project_nexus", "output/project_nexus/09-30-2026")
    assert err is not None
    assert "[PROJECT_AFFINITY_MISMATCH]" in err


def test_adversarial_category_5_exact_production_regressions():
    """Category 5: Exact production regressions - verifies the 4 exact DocIQ files that failed."""
    exact_stems = [
        "Dociq_Q4_Scope_Bailey_Amit",
        "Collateral_Dociq_Q4_Plan_Charter_Scope",
        "Dociq_Update_Q3_Accomplishments_Backfill",
        "Subject_Dociq_Q4_Scope_Executive_Strategy",
    ]
    for stem in exact_stems:
        # Title inference must resolve to dociq
        assert infer_project_from_title(stem) == "dociq"

        # derive_worktree_identity must not contain project-nexus
        wt_dir, branch = derive_worktree_identity(
            modality="DOCUMENT",
            title=stem,
            project="project_nexus",
            date="10-01-2026",
        )
        assert "dociq" in branch
        assert "project-nexus" not in branch
        assert "project_nexus" not in str(wt_dir)

        # Output hierarchy validator must block placement in output/project_nexus/
        mismatch = _validate_project_affinity(stem, "project_nexus", "output/project_nexus/10-01-2026")
        assert mismatch is not None
        assert "[PROJECT_AFFINITY_MISMATCH]" in mismatch
