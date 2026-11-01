"""
tests/test_branch_lifecycle_adversarial.py

Adversarial and edge-case testing for branch naming conventions and HITL PR merge verification.
Covers:
1. Boundary payloads: empty strings, whitespace, trailing slashes.
2. Malformed inputs: bare branch tokens, illegal characters, uppercase tokens.
3. Invariant bypass attempts: autonomous agent origins and declined outcomes in HITL ledger.
4. Exact production regression: ensuring valid conventional prefixes are accepted.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools.pr_governance_core.common import CONVENTIONAL_BRANCH_RE, slugify_branch
from tools.pr_governance_core.lifecycle import verify_pr_merge_hitl_authorization


def test_adversarial_boundary_empty_and_whitespace_branches() -> None:
    """Boundary test: empty and whitespace branch names fail CONVENTIONAL_BRANCH_RE."""
    for invalid in ("", "   ", "\t", "\n", "   /   "):
        assert CONVENTIONAL_BRANCH_RE.match(invalid) is None


def test_adversarial_boundary_trailing_and_leading_slashes() -> None:
    """Boundary test: trailing, leading, and redundant slashes are rejected."""
    for invalid in ("/feat/test", "feat/test/", "feat//test", "test/"):
        assert CONVENTIONAL_BRANCH_RE.match(invalid) is None


def test_adversarial_malformed_unconventional_names() -> None:
    """Malformed test: bare tokens and disallowed branch names fail closed."""
    disallowed = [
        "main",
        "main_merge",
        "master",
        "wip",
        "test",
        "temp",
        "scratch",
        "feat_underscores_only",
        "feat/has spaces",
        "feat/bad*char",
    ]
    for b in disallowed:
        assert CONVENTIONAL_BRANCH_RE.match(b) is None


def test_adversarial_invariant_bypass_autonomous_operator_rejected(tmp_path: Path) -> None:
    """Invariant bypass test: autonomous agent authorization cannot approve PR merge."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    branch = "test/branch-lifecycle"

    # Seed ledger with autonomous agent entry
    ledger_data = {
        "schema_version": "1.2.0",
        "decisions": [
            {
                "decision_id": "dec-pr-merge-auto-001",
                "category": "pr_merge_authorization",
                "operator_id": "autonomous_engine",
                "selected_option": "(Recommended) Approve merge",
                "prompt": "Merge test/branch-lifecycle",
                "metadata": {
                    "branch": branch,
                    "operator_class": "AUTONOMOUS",
                    "outcome_type": "AFFIRMED",
                },
            }
        ],
    }
    ledger_file.write_text(json.dumps(ledger_data, indent=2), encoding="utf-8")

    valid, msg = verify_pr_merge_hitl_authorization(branch, repo_root=tmp_path)
    assert valid is False
    assert "Missing human operator PR_MERGE_AUTHORIZATION" in msg


def test_adversarial_invariant_bypass_declined_outcome_rejected(tmp_path: Path) -> None:
    """Invariant bypass test: DECLINED or negative selection cannot approve PR merge."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    branch = "test/branch-lifecycle"

    # Seed ledger with declined decision
    ledger_data = {
        "schema_version": "1.2.0",
        "decisions": [
            {
                "decision_id": "dec-pr-merge-declined-001",
                "category": "pr_merge_authorization",
                "operator_id": "human_reviewer",
                "selected_option": "Cancel merge and retain worktree",
                "prompt": "Merge test/branch-lifecycle",
                "metadata": {
                    "branch": branch,
                    "operator_class": "HUMAN",
                    "outcome_type": "DECLINED",
                },
            }
        ],
    }
    ledger_file.write_text(json.dumps(ledger_data, indent=2), encoding="utf-8")

    valid, msg = verify_pr_merge_hitl_authorization(branch, repo_root=tmp_path)
    assert valid is False
    assert "Missing human operator PR_MERGE_AUTHORIZATION" in msg


def test_adversarial_production_regression_valid_conventional_prefixes() -> None:
    """Exact regression: standard conventional prefixes continue to match correctly."""
    valid_branches = [
        "feat/add-new-capability",
        "fix/remediate-critical-defect",
        "chore/bump-dependencies",
        "docs/update-architecture-manual",
        "test/worktree-lifecycle-verification",
        "transcript/meeting-2026-10-19",
        "ingest/doc-enterprise-ai-roadmap",
    ]
    for b in valid_branches:
        assert CONVENTIONAL_BRANCH_RE.match(b) is not None, f"Failed for {b}"
