"""tests/test_reconcile_automation_sidecars.py

Unit tests for sidecar SSOT pointer reconciliation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.reconcile_automation_sidecars import reconcile_sidecars
from tools.task_restoration_core.ssot_pointers import (
    CONSOLIDATED_TASK_ALIASES,
    MAX_POINTER_WORDS,
    TASK_SIDECAR_MAP,
    extract_target_path,
    generate_ssot_pointer_prompt,
    is_valid_pointer_prompt,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mock_sidecars_tree(tmp_path: Path) -> Path:
    """Creates a mock Antigravity sidecars directory with 6 canonical tasks."""
    sidecars_root = tmp_path / "sidecars"
    sidecars_root.mkdir()

    for task_id, info in TASK_SIDECAR_MAP.items():
        task_dir = sidecars_root / info["sidecar_dir"]
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
                f"Legacy prompt for {task_id} " * 50,  # ~250 words
            ],
            "display_name": info["title"],
        }
        sidecar_file.write_text(json.dumps(content, indent=2), encoding="utf-8")

    return sidecars_root


def test_generate_ssot_pointer_prompt() -> None:
    """Verifies that generated pointers for all 6 canonical tasks and 6 aliases are valid and under word limit."""
    for task_id, info in TASK_SIDECAR_MAP.items():
        prompt = generate_ssot_pointer_prompt(task_id, ROOT)
        assert is_valid_pointer_prompt(prompt) is True
        words = prompt.split()
        assert len(words) <= MAX_POINTER_WORDS
        target = extract_target_path(prompt)
        assert target is not None
        assert target.name == Path(info["canonical_prompt"]).name
        assert target.is_file() or (ROOT / info["canonical_prompt"]).is_file(), f"Target SSOT file does not exist: {target}"

    for alias_id, canon_id in CONSOLIDATED_TASK_ALIASES.items():
        alias_prompt = generate_ssot_pointer_prompt(alias_id, ROOT)
        canon_prompt = generate_ssot_pointer_prompt(canon_id, ROOT)
        assert alias_prompt == canon_prompt


def test_reconciliation_dry_run(mock_sidecars_tree: Path) -> None:
    """Verifies dry run reports changes without mutating files."""
    report = reconcile_sidecars(mock_sidecars_tree, ROOT, dry_run=True)
    assert report["terminal_outcome"] == "PASS"
    assert report["sidecars_reconciled"] == len(TASK_SIDECAR_MAP)

    # Ensure files were NOT modified
    for info in TASK_SIDECAR_MAP.values():
        path = mock_sidecars_tree / info["sidecar_dir"] / "sidecar.json"
        data = json.loads(path.read_text())
        assert "Legacy prompt" in data["args"][-1]


def test_reconciliation_execution(mock_sidecars_tree: Path, tmp_path: Path) -> None:
    """Verifies execution mutates sidecars to thin pointers and captures snapshots."""
    snap_dir = tmp_path / "snapshots"
    report = reconcile_sidecars(mock_sidecars_tree, ROOT, dry_run=False, snapshot_dir=snap_dir)
    assert report["terminal_outcome"] == "PASS"
    assert report["sidecars_reconciled"] == len(TASK_SIDECAR_MAP)

    # Verify mutations and snapshots
    for task_id, info in TASK_SIDECAR_MAP.items():
        path = mock_sidecars_tree / info["sidecar_dir"] / "sidecar.json"
        data = json.loads(path.read_text())
        new_prompt = data["args"][-1]
        assert is_valid_pointer_prompt(new_prompt) is True
        assert len(new_prompt.split()) < MAX_POINTER_WORDS
        assert "Legacy prompt" not in new_prompt

        snap_file = snap_dir / f"{task_id}_sidecar_pre_ssot.json"
        assert snap_file.is_file()
        assert "Legacy prompt" in snap_file.read_text()
