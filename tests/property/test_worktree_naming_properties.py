"""
tests/property/test_worktree_naming_properties.py

Hypothesis property-based tests for modality-aware worktree naming engine.
Validates invariants:
1. INV-WT-04: CONVENTIONAL_BRANCH_RE conformance for any input
2. INV-WT-03: Worktree directory name matches allowed slugify_branch conventions
3. Length bounds: slug length <= max_len
4. Character safety: only lowercase alphanumeric and single non-consecutive hyphens
5. Git ref safety: passes git check-ref-format
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from hypothesis import given, settings, strategies as st

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.worktree_naming import (
    CONVENTIONAL_BRANCH_RE,
    derive_worktree_identity,
    slugify_branch_name,
)
from tools.pr_governance_core.common import slugify_branch


@given(st.text(min_size=0, max_size=200))
@settings(max_examples=200)
def test_slugify_branch_name_invariants(raw_title: str):
    slug = slugify_branch_name(raw_title, max_len=40)

    # 1. Non-empty
    assert len(slug) > 0

    # 2. Maximum length respected
    assert len(slug) <= 40

    # 3. Only allowed characters: lowercase letters, digits, and hyphens
    assert re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug) is not None, f"Malformed slug: {slug}"

    # 4. No leading or trailing hyphens
    assert not slug.startswith("-")
    assert not slug.endswith("-")

    # 5. No consecutive hyphens
    assert "--" not in slug


@given(
    modality=st.sampled_from([
        CanonicalModality.DOCUMENT,
        CanonicalModality.AUDIO,
        CanonicalModality.MEETING_TRANSCRIPT,
        CanonicalModality.MEETING_NOTES,
        "DOCUMENT",
        "AUDIO",
        "MEETING",
        "TRANSCRIPT",
        "RAW_TRANSCRIPT",
        None,
    ]),
    title=st.text(min_size=0, max_size=150),
)
@settings(max_examples=250)
def test_derive_worktree_identity_governance_properties(modality, title: str):
    root = Path("/dummy/repo")
    wt_dir, branch = derive_worktree_identity(
        modality=modality,
        title=title,
        repo_root=root,
    )

    # 1. Conventional branch regex conformance (INV-WT-04)
    assert CONVENTIONAL_BRANCH_RE.match(branch) is not None, f"Branch '{branch}' failed CONVENTIONAL_BRANCH_RE"

    # 2. Worktree directory deterministic slug conformance (INV-WT-03)
    slug_with_prefix = slugify_branch(branch)
    slug_without_prefix = slugify_branch(branch.split("/", 1)[-1]) if "/" in branch else slug_with_prefix
    allowed_slugs = {
        slug_with_prefix,
        slug_without_prefix,
        slug_with_prefix.replace("_", "-"),
        slug_without_prefix.replace("_", "-"),
    }
    assert wt_dir.name in allowed_slugs, (
        f"Worktree directory '{wt_dir.name}' not in allowed slugs {allowed_slugs} for branch '{branch}'"
    )

    # 3. Git ref format validation
    res = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Branch '{branch}' failed git check-ref-format: {res.stderr}"
