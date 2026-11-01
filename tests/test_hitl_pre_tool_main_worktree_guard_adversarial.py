"""
tests/test_hitl_pre_tool_main_worktree_guard_adversarial.py

Adversarial and boundary test suite verifying that scripts/hitl_pre_tool_guard.py
blocks direct file writes (write_to_file / replace_file_content) targeting the primary
repository root on branch 'main' outside .worktrees/<slug>/, while permitting writes
inside worktrees or ephemeral .local/ paths.

Categories covered:
1. Boundary: Empty payload, empty target path, non-matching tools.
2. Direct-on-main: Blocked writes with exit code 2 and MAIN_WORKTREE_WRITE_BLOCKED.
3. Isolated worktree writes: Permitted writes with exit code 0.
4. Ephemeral .local/ writes: Permitted writes with exit code 0.
5. Invariant bypass: Path traversal attempts (e.g. .worktrees/slug/../../dual_pipeline/foo.py).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import pytest

from scripts.hitl_pre_tool_guard import (
    evaluate_file_write_path,
    handle_tool_call_guard,
    is_on_main_root,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_evaluate_file_write_path_blocks_main_root_tracked_paths():
    """Verify that targeting repository-tracked paths in primary root on main is blocked."""
    target_path = str(REPO_ROOT / "dual_pipeline" / "canonical_pipeline" / "pipeline.py")
    blocked, reason = evaluate_file_write_path(target_path, REPO_ROOT)
    # Note: If running inside a git checkout where root is on main, blocked is True
    if is_on_main_root(REPO_ROOT):
        assert blocked is True
        assert "MAIN_WORKTREE_WRITE_BLOCKED" in (reason or "")
    else:
        # If running from a detached state or non-main branch, evaluate_file_write_path returns False
        assert isinstance(blocked, bool)


def test_evaluate_file_write_path_permits_worktree_paths():
    """Verify that targeting paths inside .worktrees/<slug>/ is permitted."""
    wt_path = str(REPO_ROOT / ".worktrees" / "feat_rca_doc_ingest_worktree_isolation" / "pipeline.py")
    blocked, reason = evaluate_file_write_path(wt_path, REPO_ROOT)
    assert blocked is False
    assert reason is None


def test_evaluate_file_write_path_permits_local_ephemeral_state():
    """Verify that targeting paths inside .local/ runtime directory is permitted."""
    local_path = str(REPO_ROOT / ".local" / "dual_pipeline" / "active_pair.json")
    blocked, reason = evaluate_file_write_path(local_path, REPO_ROOT)
    assert blocked is False
    assert reason is None


def test_adversarial_path_traversal_resolves_to_main_and_blocks():
    """Verify that relative path traversal attempting to escape .worktrees into root main is blocked."""
    traversal_path = str(
        REPO_ROOT / ".worktrees" / "feat_rca_doc_ingest_worktree_isolation" / ".." / ".." / "dual_pipeline" / "pipeline.py"
    )
    blocked, reason = evaluate_file_write_path(traversal_path, REPO_ROOT)
    if is_on_main_root(REPO_ROOT):
        assert blocked is True
        assert "MAIN_WORKTREE_WRITE_BLOCKED" in (reason or "")


def test_adversarial_malformed_and_boundary_payloads():
    """Verify handling of empty or non-dict payloads."""
    assert handle_tool_call_guard("") is None
    assert handle_tool_call_guard("{}") is None
    assert handle_tool_call_guard("not json") is None

    # Unrelated tool call allowed
    tool_call = {
        "toolCall": {
            "name": "view_file",
            "args": {"AbsolutePath": str(REPO_ROOT / "README.md")},
        }
    }
    assert handle_tool_call_guard(json.dumps(tool_call)) is None


def test_subprocess_cli_blocking_and_exit_code_2():
    """Verify that invoking scripts/hitl_pre_tool_guard.py via subprocess exits with 2 when write is blocked."""
    guard_script = REPO_ROOT / "scripts" / "hitl_pre_tool_guard.py"
    target_file = str(REPO_ROOT / "dual_pipeline" / "canonical_pipeline" / "pipeline.py")

    payload = {
        "toolCall": {
            "name": "write_to_file",
            "args": {
                "TargetFile": target_file,
                "CodeContent": "# test",
            },
        }
    }

    proc = subprocess.run(
        [sys.executable, str(guard_script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    if is_on_main_root(REPO_ROOT):
        assert proc.returncode == 2
        out_json = json.loads(proc.stdout.strip())
        assert out_json["decision"] == "deny"
        assert "MAIN_WORKTREE_WRITE_BLOCKED" in out_json["reason"]
    else:
        assert proc.returncode in (0, 2)


@pytest.mark.parametrize(
    "tool_name",
    ["edit_file", "write_file", "multi_replace_file_content"],
)
def test_native_antigravity_tools_blocked_on_main_root(tool_name: str):
    """Verify that Antigravity native tools (edit_file, write_file) targeting main root are blocked."""
    guard_script = REPO_ROOT / "scripts" / "hitl_pre_tool_guard.py"
    target_file = str(REPO_ROOT / ".agents" / "rules" / "contract-enforcement.md")

    payload = {
        "toolCall": {
            "name": tool_name,
            "args": {
                "Path": target_file,
                "Target": "foo",
                "Replacement": "bar",
            },
        }
    }

    proc = subprocess.run(
        [sys.executable, str(guard_script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    if is_on_main_root(REPO_ROOT):
        assert proc.returncode == 2
        out_json = json.loads(proc.stdout.strip())
        assert out_json["decision"] == "deny"
        assert "MAIN_WORKTREE_WRITE_BLOCKED" in out_json["reason"]


def test_zero_jargon_governance_rule_prompt_exempt_from_margin_bypass():
    """Verify that zero-jargon rule modification questions classify as mandatory GOVERNANCE and are not bypassed."""
    from tools.check_ask_user_question import check_hook_payload

    payload = {
        "toolCall": {
            "name": "ask_question",
            "args": {
                "questions": [
                    {
                        "is_multi_select": False,
                        "options": [
                            "(Recommended) Apply the frontmatter schema fix across all 13 rule files (HIGH confidence: 95%)",
                            "Review detailed file-by-file diffs before applying any changes (MEDIUM confidence: 60%)",
                        ],
                        "question": "13 rule frontmatter YAML syntax errors identified. How would you like to proceed?",
                    }
                ]
            },
        }
    }

    res = check_hook_payload(json.dumps(payload))
    assert res.get("decision") == "allow"

