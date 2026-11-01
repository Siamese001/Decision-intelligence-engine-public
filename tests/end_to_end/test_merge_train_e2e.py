"""
End-to-End tests for Merge Train Orchestrator CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_merge_train_data_highway_cli_e2e(tmp_path: Path):
    json_path = tmp_path / "data_highway_receipt.json"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "merge_train_orchestrator.py"),
        "--data-highway",
        "feat/live-dociq_q4_scope_bailey_amit",
        "--json",
        str(json_path),
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Command failed: {res.stderr}"
    assert "DATA HIGHWAY FAST-TRACK AUDIT" in res.stdout
    assert json_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["branch"] == "feat/live-dociq_q4_scope_bailey_amit"
    assert payload["fast_track_passed"] is True


def test_merge_train_cohort_assembly_cli_e2e(tmp_path: Path):
    json_path = tmp_path / "cohort_receipt.json"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "merge_train_orchestrator.py"),
        "--cohort",
        "platform_velocity",
        "--dry-run",
        "--json",
        str(json_path),
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    # Even if simulation has conflict, the command generates receipt and executes clean lifecycle
    assert "SPECULATIVE COHORT ASSEMBLY RECEIPT" in res.stdout
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["cohort_name"] == "platform_velocity"
    assert len(payload["branches"]) >= 2
