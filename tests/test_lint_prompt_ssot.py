"""tests/test_lint_prompt_ssot.py

Adversarial and lifecycle verification tests for tools/lint_prompt_ssot.py.
Covers:
1. Clean baseline repository pass.
2. Missing canonical prompt failure.
3. Empty prompt failure.
4. Rogue un-enrolled prompt detection.
5. Task registry mismatch failure.
6. Export parity drift detection.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.lint_prompt_ssot import (
    audit_canonical_prompts,
    audit_export_parity,
    audit_registry_bindings,
    run_prompt_ssot_lint,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sandbox_repo(tmp_path: Path) -> Path:
    """Creates a temporary sandbox repository structure mirror."""
    sandbox = tmp_path / "repo"
    sandbox.mkdir()

    # Mirror automations/prompts
    src_prompts = ROOT / "automations" / "prompts"
    dst_prompts = sandbox / "automations" / "prompts"
    shutil.copytree(src_prompts, dst_prompts)

    # Mirror config/scheduled_tasks
    src_config = ROOT / "config" / "scheduled_tasks"
    dst_config = sandbox / "config" / "scheduled_tasks"
    shutil.copytree(src_config, dst_config)

    # Mirror artifacts/exports/scheduled_tasks_hardened if exists
    src_exports = ROOT / "artifacts" / "exports" / "scheduled_tasks_hardened"
    if src_exports.is_dir():
        dst_exports = sandbox / "artifacts" / "exports" / "scheduled_tasks_hardened"
        shutil.copytree(src_exports, dst_exports)

    # Mirror required governance files for tripartite check
    rule_file = ROOT / ".agents" / "rules" / "pr-queue-summary.md"
    if rule_file.is_file():
        dst_rule = sandbox / ".agents" / "rules" / "pr-queue-summary.md"
        dst_rule.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rule_file, dst_rule)

    render_file = ROOT / "tools" / "pr_governance_core" / "executive_render.py"
    if render_file.is_file():
        dst_render = sandbox / "tools" / "pr_governance_core" / "executive_render.py"
        dst_render.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(render_file, dst_render)

    return sandbox


def test_clean_baseline_passes(sandbox_repo: Path) -> None:
    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is True, f"Expected clean pass, got errors: {errors}"
    assert len(errors) == 0


def test_missing_canonical_prompt_fails(sandbox_repo: Path) -> None:
    target = sandbox_repo / "automations" / "prompts" / "graph_hygiene_agent.md"
    target.unlink()

    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is False
    assert any("Missing canonical prompt files" in e for e in errors)


def test_empty_canonical_prompt_fails(sandbox_repo: Path) -> None:
    target = sandbox_repo / "automations" / "prompts" / "pr_queue_observer_agent.md"
    target.write_text("", encoding="utf-8")

    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is False
    assert any("Canonical prompt file is empty" in e for e in errors)


def test_rogue_prompt_sprawl_detected(sandbox_repo: Path) -> None:
    rogue = sandbox_repo / "automations" / "prompts" / "unauthorized_rogue_agent.md"
    rogue.write_text("# Rogue Agent\nDo whatever.", encoding="utf-8")

    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is False
    assert any("Rogue un-enrolled prompt files detected" in e for e in errors)


def test_non_agent_suffix_detected(sandbox_repo: Path) -> None:
    bad_suffix = sandbox_repo / "automations" / "prompts" / "pr_queue_observer_prompt.md"
    bad_suffix.write_text("# Wrong Suffix\nShould end with _agent.md.", encoding="utf-8")

    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is False
    assert any("must end with '_agent.md'" in e for e in errors)


def test_registry_mismatch_fails(sandbox_repo: Path) -> None:
    reg_file = sandbox_repo / "config" / "scheduled_tasks" / "task_registry.json"
    data = json.loads(reg_file.read_text(encoding="utf-8"))
    data["tasks"]["ssot_drift_remediation"]["prompt_path"] = "automations/prompts/wrong_file.md"
    reg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is False
    assert any("prompt_path mismatch" in e for e in errors)


def test_export_drift_fails(sandbox_repo: Path) -> None:
    export_dir = sandbox_repo / "artifacts" / "exports" / "scheduled_tasks_hardened"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_file = export_dir / "weekly_market_intelligence_scheduled_task.md"
    export_file.write_text("LEGACY DUPLICATE EXPORT BODY", encoding="utf-8")

    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is False
    assert any("Legacy duplicate scheduled task export files detected" in e for e in errors)


def test_missing_worktree_or_merge_lifecycle_token_fails(sandbox_repo: Path) -> None:
    target = sandbox_repo / "automations" / "prompts" / "graph_hygiene_agent.md"
    content = target.read_text(encoding="utf-8")
    target.write_text(
        content.replace("tools/pr_governance.py rebase", "git rebase").replace("tools/pr_governance.py submit", "git merge").replace("tools/pr_governance.py merge", "git merge"),
        encoding="utf-8",
    )
    shared = sandbox_repo / "automations" / "prompts" / "_shared_governance_contract.md"
    if shared.is_file():
        shared.write_text(
            shared.read_text(encoding="utf-8").replace("tools/pr_governance.py rebase", "git rebase").replace("tools/pr_governance.py submit", "git merge").replace("tools/pr_governance.py merge", "git merge"),
            encoding="utf-8",
        )

    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is False
    assert any("tools/pr_governance.py rebase" in e for e in errors)
    assert any("missing governed PR merge token" in e for e in errors)


def test_standalone_cli_executes_without_pythonpath() -> None:
    import subprocess
    import sys

    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "lint_prompt_ssot.py"), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Standalone lint_prompt_ssot.py failed: {res.stderr}"
    assert "[PASS]" in res.stdout


def test_split_brain_shared_contract_mirror_detected(sandbox_repo: Path) -> None:
    mirror = sandbox_repo / "docs" / "governance" / "shared_automation_governance_contract.md"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text("# Divergent Shared Contract\nStale content.", encoding="utf-8")

    ok, errors = run_prompt_ssot_lint(sandbox_repo)
    assert ok is False
    assert any("INV-PROMPT-SSOT-15" in e for e in errors)

