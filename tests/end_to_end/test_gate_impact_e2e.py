"""
End-to-End tests for gate impact analyzer CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_gate_impact_cli_e2e(tmp_path: Path):
    json_path = tmp_path / "impact.json"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "gate_impact_analyzer.py"),
        "--files",
        "output/project_a/01_transcript.md",
        "output/project_a/02_recap.md",
        "--json",
        str(json_path),
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"CLI failed: {res.stderr}"
    assert "DATA_HIGHWAY" in res.stdout
    assert json_path.exists()

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["profile_name"] == "data_highway"
    assert data["layer"] == "CLIENT_DELIVERABLES"
    assert data["bypasses_bge_m3"] is True


def test_gate_impact_cli_core_engine_e2e(tmp_path: Path):
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "gate_impact_analyzer.py"),
        "--files",
        "dual_pipeline/canonical_pipeline/pipeline.py",
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0
    assert "FULL" in res.stdout
    assert "NO (Full evals required)" in res.stdout
