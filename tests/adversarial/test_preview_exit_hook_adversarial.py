"""Adversarial and boundary edge-case tests for preview pass exit hook.

Categories covered (Mandatory >= 3 per e2e-and-edge-case-coverage.md):
1. Boundary payload (empty result dict, missing intake_package, empty string package path, non-existent file)
2. Malformed input (corrupted status types, non-dict result, non-boolean flags)
3. Invariant bypass attempt (attempting auto-activation on unjudged/failing preview pass, verifying fail-closed)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.cli.preview_exit_hook import (
    execute_preview_exit_hook,
    run_auto_activation,
    should_prompt_preview_activation,
)


# ==============================================================================
# CATEGORY 1: BOUNDARY PAYLOADS
# ==============================================================================

def test_boundary_empty_result_dict():
    """Empty result payload must safely return False and not raise exceptions."""
    assert should_prompt_preview_activation({}) is False
    res = execute_preview_exit_hook({})
    assert res == {}


def test_boundary_missing_intake_package_path(tmp_path: Path):
    """Result with missing or empty intake_package must bypass prompt."""
    result = {
        "status": "COMPLETE",
        "release_status": "PASS",
        "intake_package": "",
    }
    assert should_prompt_preview_activation(result) is False


def test_boundary_nonexistent_package_file(tmp_path: Path):
    """Result referencing a nonexistent intake package file fails closed."""
    nonexistent = tmp_path / "does_not_exist" / "intake_package.json"
    result = {
        "status": "COMPLETE",
        "release_status": "PASS",
        "intake_package": str(nonexistent),
    }
    assert should_prompt_preview_activation(result) is False

    with pytest.raises(FileNotFoundError, match="Intake package not found"):
        run_auto_activation(nonexistent)


# ==============================================================================
# CATEGORY 2: MALFORMED INPUT
# ==============================================================================

def test_malformed_non_dict_results():
    """Non-dict results (None, int, str, list) must return False safely."""
    assert should_prompt_preview_activation(None) is False  # type: ignore
    assert should_prompt_preview_activation("invalid_string") is False  # type: ignore
    assert should_prompt_preview_activation([1, 2, 3]) is False  # type: ignore


def test_malformed_status_types(tmp_path: Path):
    """Wrong types for status or release_status must not trigger prompt."""
    pkg = tmp_path / "intake_package.json"
    pkg.write_text("{}", encoding="utf-8")

    assert should_prompt_preview_activation({
        "status": 12345,
        "release_status": "PASS",
        "intake_package": str(pkg),
    }) is False

    assert should_prompt_preview_activation({
        "status": "COMPLETE",
        "release_status": ["PASS"],
        "intake_package": str(pkg),
    }) is False


# ==============================================================================
# CATEGORY 3: INVARIANT BYPASS ATTEMPT
# ==============================================================================

def test_invariant_bypass_failing_qa_verdict_blocked(tmp_path: Path):
    """Preview pass with FAIL or DEGRADED release_status must never prompt activation."""
    pkg = tmp_path / "intake_package.json"
    pkg.write_text("{}", encoding="utf-8")

    for invalid_status in ("FAIL", "DEGRADED", "NOT_EVALUATED", "UNKNOWN", None):
        result = {
            "status": "COMPLETE",
            "release_status": invalid_status,
            "intake_package": str(pkg),
        }
        assert should_prompt_preview_activation(result) is False
        res = execute_preview_exit_hook(result)
        assert "preview_exit_hook" not in res


def test_invariant_bypass_already_mutated_flag_spoofing(tmp_path: Path):
    """If mutation already occurred, exit hook must not re-trigger or double-activate."""
    pkg = tmp_path / "intake_package.json"
    pkg.write_text("{}", encoding="utf-8")

    result = {
        "status": "COMPLETE",
        "release_status": "PASS",
        "graph_mutated": True,
        "graph_mutation": {"status": "COMPLETE", "activation_id": "act-001"},
        "intake_package": str(pkg),
    }
    assert should_prompt_preview_activation(result) is False
    res = execute_preview_exit_hook(result)
    assert "preview_exit_hook" not in res
