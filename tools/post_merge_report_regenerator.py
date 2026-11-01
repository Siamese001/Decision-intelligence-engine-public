#!/usr/bin/env python3
"""Post-merge report regenerator for ephemeral governance reports.

Under Wave 3 of Parallel PR Readiness:
- reports/test_timing_db.json
- reports/testing_gaps_audit_report.json
- governance/decision_intelligence/REPO_TESTING_GAPS_AUDIT_REPORT_V1.md

are untracked and ephemeral. They are automatically rebuilt following every merge commit
by the merge queue or post-merge hook without polluting feature branch diffs.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("post_merge_regenerator")


def resolve_python() -> str:
    import os

    if os.name == "nt":
        venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
        return str(venv_py) if venv_py.is_file() else sys.executable
    venv_py = ROOT / ".venv" / "bin" / "python"
    return str(venv_py) if venv_py.is_file() else sys.executable


def regenerate_testing_gaps_report(repo_root: Path, force_fresh: bool = False) -> dict[str, Any]:
    """Publishes retained qualified audit report if valid, else safely recomputes via audit_testing_gaps.py."""
    md_output = repo_root / "governance" / "decision_intelligence" / "REPO_TESTING_GAPS_AUDIT_REPORT_V1.md"
    json_output = repo_root / "reports" / "testing_gaps_audit_report.json"

    md_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.parent.mkdir(parents=True, exist_ok=True)

    if not force_fresh:
        try:
            from tools.coverage_report_reuse import publish_retained_coverage_report

            pub_res = publish_retained_coverage_report(
                repo_root=repo_root, target_md=md_output, target_json=json_output
            )
            if pub_res.get("status") == "SUCCESS" and pub_res.get("reused"):
                logger.info(
                    "Reused qualified coverage audit report (published in %.4fs without recomputation)",
                    pub_res["duration_seconds"],
                )
                return pub_res
            if pub_res.get("status") == "FAILED":
                logger.error("Failed publishing retained coverage report: %s", pub_res.get("error"))
                return pub_res
            logger.info("Retained coverage report not reusable (%s). Recomputing fresh audit...", pub_res.get("reason"))
        except Exception as err:
            logger.warning("Error checking retained coverage report: %s. Recomputing fresh audit...", err)

    py = resolve_python()
    cmd = [
        py,
        "-B",
        str(repo_root / "tools" / "audit_testing_gaps.py"),
        "--repo-root",
        str(repo_root),
        "--output",
        str(md_output),
        "--json-output",
        str(json_output),
    ]

    t0 = time.monotonic()
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    dur = time.monotonic() - t0

    if res.returncode != 0:
        logger.warning(f"audit_testing_gaps exited with code {res.returncode}:\n{res.stderr}")
        return {
            "status": "FAILED",
            "exit_code": res.returncode,
            "duration_seconds": dur,
            "error": res.stderr,
            "reused": False,
            "action": "RECOMPUTE_FAILED",
            "audit_invocations": 1,
        }

    return {
        "status": "SUCCESS",
        "exit_code": 0,
        "duration_seconds": dur,
        "markdown_report": str(md_output),
        "json_report": str(json_output),
        "reused": False,
        "action": "RECOMPUTED_FRESH",
        "audit_invocations": 1,
    }


def ensure_timing_db(repo_root: Path) -> dict[str, Any]:
    """Ensures reports/test_timing_db.json exists and is valid JSON."""
    db_path = repo_root / "reports" / "test_timing_db.json"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)
        return {"status": "INITIALIZED", "path": str(db_path), "entries": 0}

    try:
        with open(db_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            entries = len(data) if isinstance(data, dict) else 0
        return {"status": "EXISTS", "path": str(db_path), "entries": entries}
    except Exception:
        # Corrupt - reset cleanly
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)
        return {"status": "RESET_CORRUPT", "path": str(db_path), "entries": 0}


def update_ratchet_baseline_if_requested(repo_root: Path) -> dict[str, Any]:
    """Optionally burns down the file budget baseline if executed by merge queue."""
    baseline_path = repo_root / "reports" / "file_budget_burndown_baseline.json"
    if not baseline_path.exists():
        return {"status": "SKIPPED", "reason": "Baseline does not exist"}

    py = resolve_python()
    cmd = [
        py,
        "-B",
        str(repo_root / "tools" / "lint_file_budgets.py"),
        "--generate-baseline",
    ]
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    return {
        "status": "UPDATED" if res.returncode == 0 else "FAILED",
        "exit_code": res.returncode,
        "output": res.stdout,
    }


def regenerate_all_reports(
    repo_root: Path = ROOT,
    update_baseline: bool = False,
) -> dict[str, Any]:
    """Main orchestrator for rebuilding all post-merge ephemeral reports."""
    logger.info("Regenerating post-merge ephemeral reports...")
    gaps_result = regenerate_testing_gaps_report(repo_root)
    timing_result = ensure_timing_db(repo_root)

    baseline_result = None
    if update_baseline:
        baseline_result = update_ratchet_baseline_if_requested(repo_root)

    return {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "testing_gaps": gaps_result,
        "timing_db": timing_result,
        "baseline_update": baseline_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate ephemeral post-merge governance reports.")
    parser.add_argument("--repo-root", default=ROOT, type=Path, help="Path to repo root.")
    parser.add_argument("--update-baseline", action="store_true", help="Re-generate ratchet baseline if authorized.")
    args = parser.parse_args(argv)

    results = regenerate_all_reports(repo_root=args.repo_root, update_baseline=args.update_baseline)
    print(json.dumps(results, indent=2))
    return 0 if results["testing_gaps"]["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
