"""End-to-End lifecycle tests for preview pass exit hook.

Verifies:
1. Successful preview pass with PASS verdict triggers the exit hook.
2. The exit hook generates valid ask_user_question payload with assigned confidence scores.
3. Direct auto-activation executes publication and binds mutation receipt.
4. Completed runs with graph_mutated=True or non-PASS status cleanly bypass the exit hook.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.cli.preview_exit_hook import (
    EXIT_HOOK_DEFINITION,
    build_preview_activation_question,
    execute_preview_exit_hook,
    should_prompt_preview_activation,
)


@pytest.fixture
def staged_package(tmp_path: Path) -> Path:
    out_dir = tmp_path / "output" / "project_lens" / "10-06-2026" / "Review_Meeting"
    out_dir.mkdir(parents=True)
    pkg = out_dir / "intake_package.json"
    pkg.write_text(json.dumps({"package_id": "test-pkg-001", "status": "STAGED"}), encoding="utf-8")
    return pkg


def test_should_prompt_preview_activation_valid(staged_package: Path):
    result = {
        "status": "COMPLETE",
        "release_status": "PASS",
        "graph_mutated": False,
        "graph_mutation": None,
        "intake_package": str(staged_package),
        "output_dir": str(staged_package.parent),
    }
    assert should_prompt_preview_activation(result) is True


def test_should_prompt_preview_activation_bypassed_if_already_mutated(staged_package: Path):
    result = {
        "status": "COMPLETE",
        "release_status": "PASS",
        "graph_mutated": True,
        "graph_mutation": {"status": "COMPLETE"},
        "intake_package": str(staged_package),
        "output_dir": str(staged_package.parent),
    }
    assert should_prompt_preview_activation(result) is False


def test_should_prompt_preview_activation_bypassed_if_eval_failed(staged_package: Path):
    result = {
        "status": "COMPLETE",
        "release_status": "FAIL",
        "graph_mutated": False,
        "graph_mutation": None,
        "intake_package": str(staged_package),
        "output_dir": str(staged_package.parent),
    }
    assert should_prompt_preview_activation(result) is False


def test_build_preview_activation_question_payload(staged_package: Path):
    result = {
        "status": "COMPLETE",
        "release_status": "PASS",
        "intake_package": str(staged_package),
        "output_dir": str(staged_package.parent),
    }
    q = build_preview_activation_question(result, reviewer="Amit Ayer")
    assert "HITL Decision: Auto-Trigger Knowledge Graph Activation" in q["question"]
    assert len(q["options"]) == 2
    assert "(Recommended) Publish and activate into knowledge graph now" in q["options"][0]
    assert "Keep preview only without mutating graph" in q["options"][1]
    assert q["is_multi_select"] is False
    assert q["metadata"]["hook_id"] == EXIT_HOOK_DEFINITION.id
    assert q["metadata"]["reviewer"] == "Amit Ayer"


def test_execute_preview_exit_hook_emits_prompt_in_non_interactive_mode(staged_package: Path):
    result = {
        "status": "COMPLETE",
        "release_status": "PASS",
        "graph_mutated": False,
        "graph_mutation": None,
        "intake_package": str(staged_package),
        "output_dir": str(staged_package.parent),
    }
    with patch("sys.stdin.isatty", return_value=False):
        res = execute_preview_exit_hook(result, reviewer="Amit Ayer")

    assert "preview_exit_hook" in res
    assert res["preview_exit_hook"]["status"] == "PROMPT_EMITTED"
    assert res["preview_exit_hook"]["action"] == "ask_user_question"
    assert res["preview_exit_hook"]["intake_package"] == str(staged_package)


def test_execute_preview_exit_hook_auto_activate(staged_package: Path):
    result = {
        "status": "COMPLETE",
        "release_status": "PASS",
        "graph_mutated": False,
        "graph_mutation": None,
        "intake_package": str(staged_package),
        "output_dir": str(staged_package.parent),
    }
    mock_mutation = {"status": "COMPLETE", "activation_id": "act-123"}
    with patch("dual_pipeline.cli.preview_exit_hook.run_auto_activation", return_value=mock_mutation) as mock_act:
        res = execute_preview_exit_hook(result, auto_activate=True, reviewer="Amit Ayer")

    mock_act.assert_called_once_with(str(staged_package), reviewer="Amit Ayer", rationale=None)
    assert res["graph_mutated"] is True
    assert res["graph_mutation"] == mock_mutation
    assert res["preview_exit_hook"]["status"] == "AUTO_ACTIVATED"
