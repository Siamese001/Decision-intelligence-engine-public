"""tests/test_lint_automation_ssot_links.py

Adversarial and lifecycle verification tests for tools/lint_automation_ssot_links.py.
Covers:
1. Clean baseline repository pass.
2. Inlined prompt body detection (>40 words).
3. Raw markdown headers detection in sidecar prompt.
4. Broken pointer target detection.
5. Reconciliation manifest allow_inline_prompt_body violation detection.
6. Malformed sidecar args detection.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.lint_automation_ssot_links import (
    audit_host_sidecars,
    audit_reconciliation_manifest,
    run_automation_ssot_links_lint,
)
from tools.task_restoration_core.ssot_pointers import (
    TASK_SIDECAR_MAP,
    generate_ssot_pointer_prompt,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sandbox_env(tmp_path: Path) -> tuple[Path, Path]:
    """Creates a mock repo and mock sidecars environment."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Mirror automations/prompts
    src_prompts = ROOT / "automations" / "prompts"
    dst_prompts = repo / "automations" / "prompts"
    shutil.copytree(src_prompts, dst_prompts)

    # Mirror config/scheduled_tasks
    src_config = ROOT / "config" / "scheduled_tasks"
    dst_config = repo / "config" / "scheduled_tasks"
    shutil.copytree(src_config, dst_config)

    # Create mock sidecars
    sidecars_dir = tmp_path / "sidecars"
    sidecars_dir.mkdir()

    for task_id, info in TASK_SIDECAR_MAP.items():
        task_dir = sidecars_dir / info["sidecar_dir"]
        task_dir.mkdir()
        sidecar_file = task_dir / "sidecar.json"
        content = {
            "builtin": "schedule",
            "restart_policy": "always",
            "args": [
                "0 9 * * *",
                "agentapi",
                "new-conversation",
                "--",
                generate_ssot_pointer_prompt(task_id, repo),
            ],
            "display_name": info["title"],
        }
        sidecar_file.write_text(json.dumps(content, indent=2), encoding="utf-8")

    return repo, sidecars_dir


def test_clean_baseline_passes(sandbox_env: tuple[Path, Path]) -> None:
    repo, sidecars = sandbox_env
    ok, errors = run_automation_ssot_links_lint(repo, sidecars)
    assert ok is True, f"Expected clean pass, got errors: {errors}"
    assert len(errors) == 0


def test_detects_inlined_prompt_body(sandbox_env: tuple[Path, Path]) -> None:
    repo, sidecars = sandbox_env
    target_sidecar = sidecars / "pr-queue-governance-observer" / "sidecar.json"
    data = json.loads(target_sidecar.read_text())
    data["args"][4] = "This is an inlined prompt body that has far too many words. " * 10
    target_sidecar.write_text(json.dumps(data))

    errors = audit_host_sidecars(repo, sidecars)
    assert any("[INV-SSOT-LINK-02]" in err and "inlines prompt body" in err for err in errors)


def test_detects_markdown_headers_in_prompt(sandbox_env: tuple[Path, Path]) -> None:
    repo, sidecars = sandbox_env
    target_sidecar = sidecars / "knowledge-graph-hygiene-agent" / "sidecar.json"
    data = json.loads(target_sidecar.read_text())
    data["args"][4] = "Read file\n# Role\nYou are an agent."
    target_sidecar.write_text(json.dumps(data))

    errors = audit_host_sidecars(repo, sidecars)
    assert any("[INV-SSOT-LINK-02]" in err and "raw markdown headers" in err for err in errors)


def test_detects_broken_pointer_path(sandbox_env: tuple[Path, Path]) -> None:
    repo, sidecars = sandbox_env
    target_sidecar = sidecars / "ssot-configuration-deduplication-model-pinning-anti-hardcoding" / "sidecar.json"
    data = json.loads(target_sidecar.read_text())
    data["args"][4] = (
        "Read and execute the task specification defined at "
        "file:///tmp/does_not_exist/automations/prompts/ghost.md "
        "following all rules and invariants within."
    )
    target_sidecar.write_text(json.dumps(data))

    errors = audit_host_sidecars(repo, sidecars)
    assert any("[INV-SSOT-LINK-03]" in err and "targets non-existent file" in err for err in errors)


def test_detects_reconciliation_allow_inline_violation(sandbox_env: tuple[Path, Path]) -> None:
    repo, _ = sandbox_env
    recon_path = repo / "config" / "scheduled_tasks" / "registration_reconciliation.json"
    data = json.loads(recon_path.read_text())
    data["tasks"][0]["allow_inline_prompt_body"] = True  # VIOLATION
    recon_path.write_text(json.dumps(data))

    errors = audit_reconciliation_manifest(repo)
    assert any("[INV-SSOT-LINK-02]" in err and "allow_inline_prompt_body" in err for err in errors)


def test_detects_insufficient_args(sandbox_env: tuple[Path, Path]) -> None:
    repo, sidecars = sandbox_env
    target_sidecar = sidecars / "pr-queue-governance-observer" / "sidecar.json"
    data = json.loads(target_sidecar.read_text())
    data["args"] = ["0 9 * * *"]  # Missing agentapi, new-conversation, prompt
    target_sidecar.write_text(json.dumps(data))

    errors = audit_host_sidecars(repo, sidecars)
    assert any("[INV-SSOT-LINK-01]" in err and "insufficient args" in err for err in errors)


def test_reconcile_sidecars_check_mode_detects_drift(sandbox_env: tuple[Path, Path], tmp_path: Path) -> None:
    import subprocess
    import sys

    repo, sidecars = sandbox_env
    receipt = tmp_path / "receipt.json"
    script = ROOT / "tools" / "reconcile_automation_sidecars.py"

    # When sidecar cron differs from canonical map, --check must fail (exit 1)
    res_drift = subprocess.run(
        [sys.executable, str(script), "--sidecars-dir", str(sidecars), "--repo-root", str(repo), "--check", "--output-receipt", str(receipt)],
        capture_output=True,
        text=True,
    )
    assert res_drift.returncode == 1

    # Apply reconciliation first, then verify --check exits 0
    res_apply = subprocess.run(
        [sys.executable, str(script), "--sidecars-dir", str(sidecars), "--repo-root", str(repo), "--apply", "--output-receipt", str(receipt)],
        capture_output=True,
        text=True,
    )
    assert res_apply.returncode == 0

    res_clean = subprocess.run(
        [sys.executable, str(script), "--sidecars-dir", str(sidecars), "--repo-root", str(repo), "--check", "--output-receipt", str(receipt)],
        capture_output=True,
        text=True,
    )
    assert res_clean.returncode == 0


def test_dimension_findings_table_enforced_and_budget_extended() -> None:
    from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload

    options = [
        "(Recommended) Approve remediation plan (Confidence: HIGH (90%)) — Resolves all findings.",
        "Defer remediation plan (Confidence: LOW (40%)) — Retains current state.",
    ]

    # Audit/drift question without Dimension Findings Table must fail closed
    missing_table_q = "The SSOT drift remediation scan found one failing check. How should we proceed?"
    res_missing = validate_question_payload(missing_table_q, options)
    assert not res_missing.is_valid
    assert any("MISSING_DIMENSION_FINDINGS_TABLE" in e for e in res_missing.errors)

    # Audit/drift question with Dimension Findings Table (>300 chars total, <=3000 table) must pass
    with_table_q = (
        "| Dimension | Score / Status | Concrete Findings | Planned Action |\n"
        "| :--- | :--- | :--- | :--- |\n"
        "| V1-V5 Multi-SSOT Scan | 5/5 PASS | 0 violations across 5 vectors | None |\n"
        "| Drift Guard | 10/11 FAIL | reconcile_automation_sidecars --check missing | Wave 1 fix |\n"
        "| Architecture Health | 1.0/10 WARN | 19 modules >400 lines, 128 direct os.environ reads | Wave 2 fix |\n"
        "| Complexity Debt (D1) | 7.8/10 WARN | rebase_orchestrator.py & recap_grounding_healer.py CC=16 | Wave 2 fix |\n\n"
        "How should we proceed with the SSOT drift remediation plan?"
    )
    assert len(with_table_q) > 300
    res_valid = validate_question_payload(with_table_q, options)
    assert res_valid.is_valid, f"Expected valid payload with dimension table, got: {res_valid.errors}"

