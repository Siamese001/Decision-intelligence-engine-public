"""
tests/end_to_end/test_branch_lifecycle_e2e.py

End-to-End lifecycle verification for feature branch lifecycle and HITL merge authorization.
Validates:
1. Conventional branch regex accepts test/*, feat/*, fix/*, and transcript/* prefixes.
2. Deterministic worktree slug derivation via slugify_branch.
3. verify_pr_merge_hitl_authorization verifies authentic human operator authorizations.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools.pr_governance_core.common import CONVENTIONAL_BRANCH_RE, slugify_branch
from tools.pr_governance_core.lifecycle import verify_pr_merge_hitl_authorization


@pytest.mark.e2e
def test_conventional_branch_regex_matches_test_prefix() -> None:
    """Verifies that test/branch-lifecycle conforms to CONVENTIONAL_BRANCH_RE."""
    branch = "test/branch-lifecycle"
    match = CONVENTIONAL_BRANCH_RE.match(branch)
    assert match is not None
    assert match.group(1) == "test"


@pytest.mark.e2e
def test_slugify_branch_derivation() -> None:
    """Verifies that slugify_branch generates valid directory slug."""
    branch = "test/branch-lifecycle"
    slug = slugify_branch(branch)
    assert slug == "test_branch_lifecycle"


@pytest.mark.e2e
def test_verify_pr_merge_hitl_authorization_lifecycle(tmp_path: Path) -> None:
    """End-to-end verification of HITL authorization discovery in candidate ledgers."""
    ledger_file = tmp_path / "hitl_decision_ledger.json"
    branch = "test/branch-lifecycle"

    # Seed ledger with valid human operator authorization
    ledger_data = {
        "schema_version": "1.2.0",
        "decisions": [
            {
                "decision_id": "dec-pr-merge-test-001",
                "category": "pr_merge_authorization",
                "operator_id": "operator_explicit_ask_question",
                "selected_option": "(Recommended) Approve and execute governed merge into main — Confidence: HIGH (95%)",
                "prompt": "Ready to merge test/branch-lifecycle into main. How would you like to proceed?",
                "metadata": {
                    "branch": branch,
                    "operator_class": "HUMAN",
                    "outcome_type": "AFFIRMED",
                },
            }
        ],
    }
    ledger_file.write_text(json.dumps(ledger_data, indent=2), encoding="utf-8")

    # Run check against temporary target repo root
    valid, msg = verify_pr_merge_hitl_authorization(branch, repo_root=tmp_path)
    assert valid is True
    assert "Verified human PR merge authorization" in msg
