"""Wave 2 Counterfactual Regression Suite: Monolithic Module Budget & Exclusion Ratchet Burndown.

Verifies:
1. Target modules (`pptx_source.py`, `drift_guard.py`, `transcript_duration_helper.py`, `preflight_coordinator.py`, `merkle_cache.py`) are each strictly <= 400 lines.
2. `dual_pipeline/drift_guard.py` is removed from `config/governance/file_budget_exclusions.json`.
3. `reports/file_budget_burndown_baseline.json` is ratcheted down to <= 87 modules.
4. Public APIs and core behaviors of `pptx_source`, `drift_guard`, and `transcript_duration_helper` remain 100% intact.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

WAVE2_MODULES = [
    "dual_pipeline/pptx_source.py",
    "dual_pipeline/drift_guard.py",
    "dual_pipeline/transcript_duration_helper.py",
    "dual_pipeline/transcript_pipeline_core/preflight_coordinator.py",
    "dual_pipeline/semantic_cache_core/merkle_cache.py",
]


@pytest.mark.parametrize("rel_path", WAVE2_MODULES)
def test_wave2_module_line_budget_under_400(rel_path: str) -> None:
    """Each Wave 2 target module must satisfy INV-BUDGET-FILE-LINES (<= 400 lines)."""
    path = REPO_ROOT / rel_path
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    assert line_count <= 400, f"{rel_path} has {line_count} lines (exceeds 400-line clean budget)"


def test_wave2_drift_guard_removed_from_exclusions() -> None:
    """dual_pipeline/drift_guard.py must no longer require an exclusion in file_budget_exclusions.json."""
    excl_path = REPO_ROOT / "config/governance/file_budget_exclusions.json"
    data = json.loads(excl_path.read_text(encoding="utf-8"))
    excluded_paths = [item.get("path") for item in data.get("exclusions", [])]
    assert "dual_pipeline/drift_guard.py" not in excluded_paths


def test_wave2_burndown_baseline_ratcheted() -> None:
    """reports/file_budget_burndown_baseline.json must ratchet down to <= 87 oversized modules."""
    base_path = REPO_ROOT / "reports/file_budget_burndown_baseline.json"
    data = json.loads(base_path.read_text(encoding="utf-8"))
    assert data["total_oversized_modules"] <= 87
    assert len(data["modules"]) <= 87
    assert "dual_pipeline/transcript_pipeline_core/preflight_coordinator.py" not in data["modules"]
    assert "dual_pipeline/semantic_cache_core/merkle_cache.py" not in data["modules"]


def test_wave2_functional_parity() -> None:
    """Verify functional parity of compacted modules."""
    from dual_pipeline.pptx_source import PPTXSlide, sha256_bytes
    from dual_pipeline.drift_guard import DriftError, pair_binding
    from dual_pipeline.transcript_duration_helper import (
        compute_materiality_budget_bonus,
        parse_time_duration_minutes,
        resolve_duration_mutation_budget,
        resolve_meeting_duration,
    )
    from dual_pipeline.semantic_cache_core.merkle_cache import compute_subgraph_merkle_root

    slide = PPTXSlide(slide_number=1, slide_target="ppt/slides/slide1.xml", title="Intro", paragraphs=["Hello world"])
    md = slide.to_markdown()
    assert "## Slide 1: Intro" in md and "Hello world" in md
    assert len(sha256_bytes(b"test")) == 64

    with pytest.raises(DriftError):
        pair_binding({"generation_id": "g1"})

    assert parse_time_duration_minutes("04:00 PM - 04:45 PM ET") == 45
    assert resolve_meeting_duration({"duration_minutes": 35}, "") == 35
    assert compute_materiality_budget_bonus({"rigor_tier": "TIER_1_DEEP_ARCHITECTURE"}, "architecture scada") >= 20
    assert resolve_duration_mutation_budget({"duration_minutes": 60}, "architecture") >= 65
    assert len(compute_subgraph_merkle_root([{"id": "n1"}])) == 64
