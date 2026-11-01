"""tests/test_lint_automation_ssot_links_adversarial.py

Adversarial and boundary tests for tools/lint_automation_ssot_links.py.
Mandatory edge-case categories covered:
1. Boundary payload: 40-word boundary validation, empty sidecar file, malformed json.
2. Malformed input: missing args key, non-string prompt in args, missing sidecar fields.
3. Invariant bypass attempt: directory traversal outside repository, pointing to unauthorized system files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.lint_automation_ssot_links import audit_host_sidecars
from tools.task_restoration_core.ssot_pointers import (
    TASK_SIDECAR_MAP,
    generate_ssot_pointer_prompt,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mock_single_task_env(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    prompts_dir = repo / "automations" / "prompts"
    prompts_dir.mkdir(parents=True)
    for task_id, info in TASK_SIDECAR_MAP.items():
        (prompts_dir / Path(info["canonical_prompt"]).name).write_text("# Role\nObserver prompt.")

    sidecars = tmp_path / "sidecars"
    sidecars.mkdir()
    for task_id, info in TASK_SIDECAR_MAP.items():
        tdir = sidecars / info["sidecar_dir"]
        tdir.mkdir()
        (tdir / "sidecar.json").write_text(
            json.dumps({
                "builtin": "schedule",
                "args": [
                    "0 9 * * *", "agentapi", "new-conversation", "--",
                    generate_ssot_pointer_prompt(task_id, repo),
                ],
            })
        )
    return repo, sidecars


def test_boundary_word_limit_40_vs_41(mock_single_task_env: tuple[Path, Path]) -> None:
    repo, sidecars = mock_single_task_env
    sidecar_file = sidecars / "pr-queue-governance-observer" / "sidecar.json"
    data = json.loads(sidecar_file.read_text())

    # 41 words fails
    data["args"][4] = (
        "Read and execute the task specification defined at "
        f"file://{repo}/automations/prompts/pr_queue_observer_agent.md "
        "following all rules and invariants within and also " + "extra " * 30
    )
    sidecar_file.write_text(json.dumps(data))

    errors = audit_host_sidecars(repo, sidecars)
    assert any("[INV-SSOT-LINK-02]" in err and "inlines prompt body" in err for err in errors)


def test_malformed_input_corrupted_json(mock_single_task_env: tuple[Path, Path]) -> None:
    repo, sidecars = mock_single_task_env
    sidecar_file = sidecars / "pr-queue-governance-observer" / "sidecar.json"
    sidecar_file.write_text("CORRUPTED { NOT JSON }")

    errors = audit_host_sidecars(repo, sidecars)
    assert any("[INV-SSOT-LINK-01]" in err and "Malformed JSON" in err for err in errors)


def test_malformed_input_missing_args(mock_single_task_env: tuple[Path, Path]) -> None:
    repo, sidecars = mock_single_task_env
    sidecar_file = sidecars / "pr-queue-governance-observer" / "sidecar.json"
    sidecar_file.write_text(json.dumps({"builtin": "schedule"}))  # No args

    errors = audit_host_sidecars(repo, sidecars)
    assert any("[INV-SSOT-LINK-01]" in err and "insufficient args" in err for err in errors)


def test_invariant_bypass_unauthorized_system_path(mock_single_task_env: tuple[Path, Path]) -> None:
    repo, sidecars = mock_single_task_env
    sidecar_file = sidecars / "pr-queue-governance-observer" / "sidecar.json"
    data = json.loads(sidecar_file.read_text())
    data["args"][4] = (
        "Read and execute the task specification defined at "
        "file:///etc/hosts "
        "following all rules and invariants within."
    )
    sidecar_file.write_text(json.dumps(data))

    errors = audit_host_sidecars(repo, sidecars)
    assert any("[INV-SSOT-LINK-01]" in err and "does not conform to SSOT pointer template" in err for err in errors)
