"""Wave 5 prompt/export binding and table-only CLI contract checks."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = [
    "Priority",
    "Branch",
    "Category",
    "Complexity",
    "Code Completed?",
    "Rebase Required?",
    "PR to Main Required?",
    "Graph Mutated (Y/N)",
    "Recommended Disposition",
    "Immediate Blocker / Action",
]


def test_prompt_export_and_registry_bind_both_modes_to_table_contract() -> None:
    prompt = (ROOT / "automations/prompts/pr_queue_observer_agent.md").read_bytes()
    registry = json.loads((ROOT / "config/scheduled_tasks/task_registry.json").read_text(encoding="utf-8"))
    task = registry["tasks"]["pr_queue_observer"]
    assert task["default_mode"] == "interactive"
    assert "interactive" in task["allowed_modes"]
    assert task["output_policy"]["formats"] == ["table"]
    contract = json.loads((ROOT / "config/scheduled_tasks/pr_queue_output_contract.json").read_text(encoding="utf-8"))
    assert contract["output"]["columns"] == EXPECTED
    assert "--format table" in prompt.decode()
    assert task["registration_status"]["status"] in ("UNRESOLVED", "ACTIVE", "REGISTERED")


def test_invalid_cli_mode_still_returns_only_the_contract_table() -> None:
    script = ROOT / "tools/pr_queue_observer.py"
    result = subprocess.run(
        [sys.executable, "-B", str(script), "--mode", "unsupported", "--format", "table"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 2
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines[0] == "| " + " | ".join(EXPECTED) + " |"
    assert all(line.startswith("|") and line.endswith("|") for line in lines)
