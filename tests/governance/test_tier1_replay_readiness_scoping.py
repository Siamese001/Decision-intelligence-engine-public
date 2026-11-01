#!/usr/bin/env python3
"""Governance unit and integration tests for path-scoped Tier 1 cached replay."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from tools.readiness_core import _build_core_test_modules, _build_stages, has_tier1_replay_scope
from tools.verify_tier1_cached_replay import (
    has_tier1_replay_scope as runner_has_scope,
    verify_tier1_cached_replay,
)


def test_has_tier1_replay_scope_activation() -> None:
    """Verify that all pipeline and transcript paths activate replay scope."""
    pipeline_paths = [
        "dual_pipeline/transcript_stages_core/x_gates.py",
        "dual_pipeline/transcript_qa_core/healing_mesh.py",
        "dual_pipeline/canonical_pipeline/worktree_inheritance.py",
        "pipeline_a_intake/audio_ingestion.py",
        "pipeline_b_extraction/entity_extractor.py",
        "tests/test_live_meeting_recording.py",
        "dual_pipeline/copilot/engine.py",
    ]
    for path in pipeline_paths:
        assert has_tier1_replay_scope([path]) is True, f"Failed to activate scope for {path}"
        assert runner_has_scope([path]) is True, f"Runner failed to activate scope for {path}"


def test_has_tier1_replay_scope_exemption() -> None:
    """Verify that unrelated paths (docs, UI, storage, schemas) are exempted."""
    unrelated_paths = [
        "docs/implementation_plan.md",
        "documentation/architecture.md",
        "storage/connection.py",
        "tools/figma_layout.py",
        "reports/readiness/receipt.json",
        "config/governance/file_budget_exclusions.json",
    ]
    for path in unrelated_paths:
        assert has_tier1_replay_scope([path]) is False, f"Unexpected scope activation for {path}"
        assert runner_has_scope([path]) is False, f"Runner unexpected scope activation for {path}"


def test_readiness_core_test_module_scoping() -> None:
    """Verify _build_core_test_modules enrolls replay tests only when scoped."""
    pipeline_targets = ["dual_pipeline/transcript_stages_core/x_gates.py"]
    modules_pipeline = _build_core_test_modules(pipeline_targets, full=False)
    assert "tests.end_to_end.test_tier1_cached_replay_gate_e2e" in modules_pipeline
    assert "tests.adversarial.test_tier1_cached_replay_adversarial" in modules_pipeline

    unrelated_targets = ["docs/plan.md", "storage/connection.py"]
    modules_unrelated = _build_core_test_modules(unrelated_targets, full=False)
    assert "tests.end_to_end.test_tier1_cached_replay_gate_e2e" not in modules_unrelated
    assert "tests.adversarial.test_tier1_cached_replay_adversarial" not in modules_unrelated


def test_readiness_core_build_stages_scoping() -> None:
    """Verify _build_stages includes Tier 1 cached replay stage only when scoped."""
    py = sys.executable
    pipeline_targets = ["dual_pipeline/canonical_pipeline/pipeline.py"]
    stages_pipeline = _build_stages(py, pipeline_targets, test_modules=[])
    stage_names_pipeline = [name for name, _ in stages_pipeline]
    assert "Tier 1 Cached Stage Replay Gate" in stage_names_pipeline

    unrelated_targets = ["documentation/overview.md"]
    stages_unrelated = _build_stages(py, unrelated_targets, test_modules=[])
    stage_names_unrelated = [name for name, _ in stages_unrelated]
    assert "Tier 1 Cached Stage Replay Gate" not in stage_names_unrelated


def test_verify_tier1_cached_replay_bypasses_on_non_pipeline_files() -> None:
    """Verify runner bypasses with 0.00s latency on non-pipeline targets."""
    success, receipt = verify_tier1_cached_replay(target_files=["docs/plan.md"])
    assert success is True
    assert receipt.get("bypassed") is True
    assert receipt.get("verdict") == "BYPASS"
    assert receipt.get("duration_seconds") == 0.0


def test_verify_tier1_cached_replay_executes_on_pipeline_files() -> None:
    """Verify runner executes hermetic replay and checks invariants on pipeline targets."""
    success, receipt = verify_tier1_cached_replay(
        target_files=["dual_pipeline/transcript_stages_core/x_gates.py"]
    )
    assert success is True
    assert receipt.get("bypassed") is False
    assert receipt.get("verdict") == "PASS"
    assert receipt.get("date_normalized") == "10/16/2026"
    assert receipt.get("rebound_paths_verified") is True
    assert receipt.get("x3_coherence_verified") is True
