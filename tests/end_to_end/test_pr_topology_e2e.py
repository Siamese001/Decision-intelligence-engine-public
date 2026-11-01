"""
End-to-End lifecycle test for PR Topology Auditor.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_pr_topology_auditor_e2e(tmp_path: Path):
    json_path = tmp_path / "topology_matrix.json"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "pr_topology_auditor.py"),
        "--json",
        str(json_path),
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Command failed: {res.stderr}"
    assert "BRANCH TOPOLOGY & CONTENTION AUDIT REPORT" in res.stdout
    assert json_path.exists(), "JSON output file not generated"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "timestamp" in payload
    assert "topology_matrix" in payload
    assert "clusters" in payload
    assert "metrics" in payload
    assert payload["metrics"]["branch_count"] > 0
    assert payload["metrics"]["cohort_rebase_cycles"] > 0
