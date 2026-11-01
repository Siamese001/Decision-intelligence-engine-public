"""
End-to-End tests for git semantic merge driver CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_semantic_merge_driver_cli_python_e2e(tmp_path: Path):
    ancestor = tmp_path / "ancestor_constants.py"
    ours = tmp_path / "ours_constants.py"
    theirs = tmp_path / "theirs_constants.py"

    ancestor.write_text('CONTROLLED_FILES: Set[str] = {"core.py"}\n', encoding="utf-8")
    ours.write_text(
        'CONTROLLED_FILES: Set[str] = {\n    "core.py",\n    "ours_feat.py",\n}\n', encoding="utf-8"
    )
    theirs.write_text(
        'CONTROLLED_FILES: Set[str] = {\n    "core.py",\n    "theirs_feat.py",\n}\n',
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "semantic_merge_driver.py"),
        str(ancestor),
        str(ours),
        str(theirs),
        "tools/validate_output_hierarchy_core/constants.py",
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Driver failed: {res.stderr}"

    merged_content = ours.read_text(encoding="utf-8")
    assert '"ours_feat.py"' in merged_content
    assert '"theirs_feat.py"' in merged_content
    assert '"core.py"' in merged_content


def test_semantic_merge_driver_cli_json_e2e(tmp_path: Path):
    ancestor = tmp_path / "ancestor.json"
    ours = tmp_path / "ours.json"
    theirs = tmp_path / "theirs.json"

    ancestor.write_text('{"base_key": 1}', encoding="utf-8")
    ours.write_text('{"base_key": 1, "k1": "v1"}', encoding="utf-8")
    theirs.write_text('{"base_key": 1, "k2": "v2"}', encoding="utf-8")

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "semantic_merge_driver.py"),
        str(ancestor),
        str(ours),
        str(theirs),
        "config/governance/project_registry.json",
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"Driver failed: {res.stderr}"

    data = json.loads(ours.read_text(encoding="utf-8"))
    assert data["base_key"] == 1
    assert data["k1"] == "v1"
    assert data["k2"] == "v2"


def test_semantic_merge_driver_cli_conflict_fails_closed(tmp_path: Path):
    ancestor = tmp_path / "ancestor.json"
    ours = tmp_path / "ours.json"
    theirs = tmp_path / "theirs.json"

    ancestor.write_text('{"conflict_key": "v0"}', encoding="utf-8")
    ours.write_text('{"conflict_key": "v1"}', encoding="utf-8")
    theirs.write_text('{"conflict_key": "v2"}', encoding="utf-8")

    cmd = [
        sys.executable,
        str(ROOT / "tools" / "semantic_merge_driver.py"),
        str(ancestor),
        str(ours),
        str(theirs),
        "config/governance/project_registry.json",
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 1, "Driver should return 1 on conflicting keys"
