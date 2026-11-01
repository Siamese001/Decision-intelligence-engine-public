"""
tools/active_ingestion_acceptance_core/helpers.py

Shared verification, predecessor validation, and execution helpers for
active ingestion acceptance runners.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from dual_pipeline.core import digest, read_json, sha256_file


def git_info(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args], text=True, timeout=60
    ).strip()


def seal_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    copy_payload = dict(payload)
    copy_payload.pop("canonical_digest", None)
    copy_payload["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(copy_payload),
    }
    return copy_payload


def verify_predecessor(root: Path, predecessor_path: Path, expected_wave: int) -> dict[str, Any]:
    if not predecessor_path.is_file():
        raise RuntimeError(f"Missing required parent receipt: {predecessor_path}")
    data = read_json(predecessor_path)
    if not isinstance(data, dict) or data.get("wave") != expected_wave or data.get("terminal_outcome") != "PASS":
        raise RuntimeError(f"Invalid or non-PASS predecessor receipt: {predecessor_path}")
    return {
        "path": predecessor_path.relative_to(root).as_posix() if predecessor_path.is_relative_to(root) else str(predecessor_path),
        "sha256": sha256_file(predecessor_path),
        "wave": expected_wave,
    }


def collect_deliverables(root: Path, deliverables: tuple[str, ...]) -> list[dict[str, str]]:
    rows = []
    for rel_path in deliverables:
        full_path = root / rel_path
        if not full_path.is_file():
            raise RuntimeError(f"Missing mandatory deliverable: {rel_path}")
        rows.append({"path": rel_path, "sha256": sha256_file(full_path)})
    return rows


def check_line_budgets(root: Path, rel_paths: tuple[str, ...]) -> None:
    from tools.file_budget_core import evaluate_module_file

    for rel_path in rel_paths:
        full_path = root / rel_path
        verdict = evaluate_module_file(full_path, repo_root=root)
        if verdict.is_blocking:
            raise RuntimeError(f"Module {rel_path} violated module size policy: {verdict.reason}")


def check_decision_gate(root: Path, wave: int, plan_path: Path) -> dict[str, Any]:
    cmd = [
        sys.executable, str(root / "tools" / "pre_wave_decision_gate.py"),
        "--wave", str(wave), "--plan-path", str(plan_path),
        "--allow-zero-decisions", "--json",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=root, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"Pre-wave decision gate failed:\n{res.stdout}\n{res.stderr}")
    gate_json = json.loads(res.stdout)
    if not gate_json.get("clearance_granted"):
        raise RuntimeError("Decision gate clearance was not granted.")
    return gate_json


def run_pytest(root: Path, test_files: list[str], timeout: int) -> int:
    cmd = [sys.executable, "-m", "pytest", *test_files, f"--timeout={timeout}"]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=root, timeout=timeout + 30)
    if res.returncode != 0:
        raise RuntimeError(f"Tests failed:\n{res.stdout}\n{res.stderr}")
    return res.returncode
