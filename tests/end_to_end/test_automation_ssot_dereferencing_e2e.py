"""tests/end_to_end/test_automation_ssot_dereferencing_e2e.py

End-to-end integration and lifecycle verification for Antigravity automation SSOT pointer dereferencing.
Verifies that:
1. All 7 host sidecars in ~/.gemini/config/sidecars/ contain valid thin pointers.
2. When launched, the agent dereferences the target URI directly from disk.
3. Modifications to the master repository prompt immediately reflect in runtime without touching sidecars.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.task_restoration_core.ssot_pointers import (
    MAX_POINTER_WORDS,
    RETIRED_SIDECAR_DIRS,
    TASK_SIDECAR_MAP,
    extract_target_path,
    is_valid_pointer_prompt,
    resolve_primary_repo_root,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def host_sidecars_dir() -> Path:
    p = Path.home() / ".gemini" / "config" / "sidecars"
    assert p.is_dir(), f"Antigravity sidecars directory not found: {p}"
    return p


def test_all_eight_host_sidecars_dereference_cleanly(host_sidecars_dir: Path) -> None:
    """E2E Test: All 17 host sidecars dereference to existing, non-empty canonical prompts and 7 retired sidecars are pruned."""
    assert len(TASK_SIDECAR_MAP) == 17
    for retired in RETIRED_SIDECAR_DIRS:
        assert not (host_sidecars_dir / retired).exists(), f"Retired sidecar still exists: {retired}"

    for task_id, info in TASK_SIDECAR_MAP.items():
        sidecar_path = host_sidecars_dir / info["sidecar_dir"] / "sidecar.json"
        assert sidecar_path.is_file(), f"Missing host sidecar for {task_id}: {sidecar_path}"

        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        args = data.get("args", [])
        assert len(args) >= 5, f"Sidecar {sidecar_path} has insufficient args"
        assert args[1] == "agentapi"
        assert args[2] == "new-conversation"
        assert args[-2] == "--"
        if info.get("extra_args"):
            for extra in info["extra_args"]:
                assert extra in args

        pointer_prompt = args[-1]
        assert is_valid_pointer_prompt(pointer_prompt) is True
        assert len(pointer_prompt.split()) <= MAX_POINTER_WORDS
        assert "\n#" not in pointer_prompt

        # Simulate agent runtime dereferencing the prompt (supporting worktree isolation)
        target_path = extract_target_path(pointer_prompt)
        assert target_path is not None, f"Failed to extract target path from {pointer_prompt}"
        effective_path = target_path if target_path.is_file() else (ROOT / info["canonical_prompt"])
        assert effective_path.is_file(), f"Dereferenced target path does not exist: {target_path} (or {effective_path})"

        # Read actual dereferenced prompt content
        full_prompt = effective_path.read_text(encoding="utf-8")
        assert len(full_prompt) > 200, f"Target prompt unexpectedly small: {len(full_prompt)} chars"
        assert "<!-- GOVERNED COMMON CONTRACT" in full_prompt or "# Role" in full_prompt
        if not info["canonical_prompt"].startswith("automations/prompts/sidecars/"):
            worktree_prompt = (ROOT / info["canonical_prompt"]).read_text(encoding="utf-8")
            assert "INV-RUNTIME-PROOF-5POINT" in worktree_prompt
            assert "| Target File:Lines | Runtime Command | Pre-Fix Observed Behavior | Post-Fix Observed Behavior | Independent State Readback |" in worktree_prompt


def test_runtime_prompt_update_reflection(tmp_path: Path) -> None:
    """E2E Test: Modifying the master prompt file is immediately read by runtime dereferencing."""
    # Create mock repo and target prompt
    mock_repo = tmp_path / "repo"
    prompts_dir = mock_repo / "automations" / "prompts"
    prompts_dir.mkdir(parents=True)
    prompt_file = prompts_dir / "pr_queue_observer_agent.md"
    prompt_file.write_text("# Role v1.0\nInitial prompt specification.")

    # Create mock sidecar with thin pointer
    sidecar_dir = tmp_path / "sidecar_dir"
    sidecar_dir.mkdir()
    sidecar_file = sidecar_dir / "sidecar.json"
    pointer_text = (
        f"Read and execute the task specification defined at file://{prompt_file.resolve()} "
        "following all rules and invariants within."
    )
    sidecar_file.write_text(
        json.dumps({
            "builtin": "schedule",
            "args": ["0 9 * * *", "agentapi", "new-conversation", "--", pointer_text],
        })
    )

    # Agent launch 1: read initial version
    sidecar_data = json.loads(sidecar_file.read_text())
    target_1 = extract_target_path(sidecar_data["args"][4])
    assert target_1 is not None
    assert "v1.0" in target_1.read_text()

    # In-place update to repository SSOT prompt (e.g. via git pull or merge)
    prompt_file.write_text("# Role v2.0\nUpdated prompt specification with new governance rules.")

    # Agent launch 2: read updated version WITHOUT touching sidecar.json
    sidecar_data_2 = json.loads(sidecar_file.read_text())
    assert sidecar_data_2["args"][4] == pointer_text  # Sidecar was not modified
    target_2 = extract_target_path(sidecar_data_2["args"][4])
    assert target_2 is not None
    assert "v2.0" in target_2.read_text()
    assert "v1.0" not in target_2.read_text()


def test_ssot_drift_remediation_prompt_enforces_dimension_table_and_8000_budget() -> None:
    """E2E Test: Master ssot_drift_remediation_agent.md mandates Dimension Findings Table and 8000-char budget."""
    prompt_path = ROOT / "automations" / "prompts" / "ssot_drift_remediation_agent.md"
    content = prompt_path.read_text(encoding="utf-8")
    assert "MISSING_DIMENSION_FINDINGS_TABLE" in content
    assert "MAX_FINDINGS_TABLE_LENGTH = 8000" in content
    assert "| Dimension | Score / Status | Concrete Findings | Planned Action |" in content
    assert "tools/audit_governance_harmonization.py" in content
    assert "INV-WT-EPHEMERAL-SCHEDULED" in content


