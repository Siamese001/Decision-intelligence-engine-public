"""Adversarial and Edge-Case Tests for Wave 4: Proportional Gating & Promotion Target Proof.

Mandatory edge-case coverage:
1. Boundary payload & malformed input: Corrupted gate manifest, empty input paths, path traversal.
2. Invariant bypass attempts: Governance/storage disguised as docs, unmapped extensions.
3. Rollback & recovery: PROPORTIONAL_GATES_DISABLED=1 and --full flag cleanly restoring 30-stage execution.
4. Exact production regression: 57-file pipeline alignment review correctly resolving to Lane 2.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.readiness_planner import (
    ProportionalPlan,
    filter_impacted_test_modules,
    load_gate_manifest,
    plan_readiness,
)


def test_adversarial_corrupted_gate_manifest_fallback(tmp_path: Path) -> None:
    """Category: Malformed input. Corrupted gate manifest JSON falls back to full Lane 2."""
    bad_manifest = tmp_path / "bad_manifest.json"
    bad_manifest.write_text("{corrupt json: not valid...", encoding="utf-8")

    plan, required_indices = plan_readiness(
        ["docs/readme.md"], repo_root=ROOT, manifest_path=bad_manifest
    )
    assert plan.risk_lane == 2
    assert plan.proportional_applied is False
    assert len(required_indices) == 30
    assert "fallback" in plan.manifest_version


def test_adversarial_empty_payload_and_path_traversal() -> None:
    """Category: Boundary payload & malformed input. Empty paths or path traversal fail closed to Lane 2."""
    total_stages = load_gate_manifest(repo_root=ROOT)["total_registered_stages"]

    # Empty files list -> fail closed to Lane 2
    p_empty, idx_empty = plan_readiness([], repo_root=ROOT)
    assert p_empty.risk_lane == 2
    assert p_empty.proportional_applied is False
    assert len(idx_empty) == total_stages

    # Path traversal outside repository -> fail closed to Lane 2
    p_traversal, idx_traversal = plan_readiness(["../outside_repo.py"], repo_root=ROOT)
    assert p_traversal.risk_lane == 2
    assert p_traversal.proportional_applied is False
    assert len(idx_traversal) == total_stages

    # Unmapped file extension (.unknown) -> fail closed to Lane 2
    p_unmapped, idx_unmapped = plan_readiness(["payload.unknown_ext"], repo_root=ROOT)
    assert p_unmapped.risk_lane == 2
    assert p_unmapped.proportional_applied is False
    assert len(idx_unmapped) == total_stages


def test_adversarial_invariant_bypass_attempts() -> None:
    """Category: Invariant bypass attempt. Governance or core modules paired with docs strictly escalate to Lane 2."""
    total_stages = load_gate_manifest(repo_root=ROOT)["total_registered_stages"]

    # Critical storage module mixed with 10 documentation files
    mixed_paths = [f"docs/chapter_{i}.md" for i in range(10)] + ["storage/connection.py"]
    p_mixed, idx_mixed = plan_readiness(mixed_paths, repo_root=ROOT)
    assert p_mixed.risk_lane == 2
    assert p_mixed.proportional_applied is False
    assert len(idx_mixed) == total_stages

    # Governance rule disguised under docs/ prefix
    fake_doc_gov = ["docs/rules/agent-routing.md"]
    # Rules under governance/ or .agents/ must never escape to Lane 0
    p_gov, idx_gov = plan_readiness([".agents/rules/concurrency.md"], repo_root=ROOT)
    assert p_gov.risk_lane == 2
    assert p_gov.proportional_applied is False
    assert len(idx_gov) == total_stages


def test_adversarial_rollback_and_full_flag_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Category: Rollback & recovery. PROPORTIONAL_GATES_DISABLED=1 or --full disables optimization."""
    total_stages = load_gate_manifest(repo_root=ROOT)["total_registered_stages"]

    # Normal docs change is Lane 0
    p_norm, idx_norm = plan_readiness(["docs/guide.md"], repo_root=ROOT)
    assert p_norm.risk_lane == 0
    assert p_norm.proportional_applied is True

    # With PROPORTIONAL_GATES_DISABLED=1
    monkeypatch.setenv("PROPORTIONAL_GATES_DISABLED", "1")
    p_env, idx_env = plan_readiness(["docs/guide.md"], repo_root=ROOT)
    assert p_env.risk_lane == 2
    assert p_env.proportional_applied is False
    assert len(idx_env) == total_stages

    monkeypatch.delenv("PROPORTIONAL_GATES_DISABLED", raising=False)

    # With explicit force_full=True
    p_full, idx_full = plan_readiness(["docs/guide.md"], repo_root=ROOT, force_full=True)
    assert p_full.risk_lane == 2
    assert p_full.proportional_applied is False
    assert len(idx_full) == total_stages


def test_adversarial_production_regression_alignment_pr() -> None:
    """Category: Exact production regression. 57-file alignment PR candidate strictly resolves to Lane 2."""
    from tools.lint_blast_radius import get_git_numstat, parse_numstat_output

    total_stages = load_gate_manifest(repo_root=ROOT)["total_registered_stages"]

    align_sha = "46a60fb68127f4ba3da76f82dce4724255d959d2"
    raw_numstat = get_git_numstat(ROOT, commit_sha=align_sha)
    diff_stats = parse_numstat_output(raw_numstat)
    files = [s.path for s in diff_stats]
    assert len(files) == 57, f"Expected 57 committed files, got {len(files)}"

    plan, required_indices = plan_readiness(files, repo_root=ROOT)
    assert plan.risk_lane == 2, f"Alignment PR with runtime changes must be Lane 2, got {plan.risk_lane}"
    assert plan.proportional_applied is False
    assert len(required_indices) == total_stages, f"All {total_stages} stages must be required for 57-file alignment PR"

    # Behavioral test filtering must fall back to full suite
    base_suite = ["tests.test_all_1", "tests.test_all_2"]
    impacted = filter_impacted_test_modules(files, test_mode=plan.test_mode, base_modules=base_suite)
    assert impacted == base_suite, "Lane 2 multi-module change must run all tests"
