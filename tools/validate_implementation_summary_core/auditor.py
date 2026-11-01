"""Candidate discovery and wave completion auditing for implementation summaries and plans."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def find_plan_candidates(repo_root: Path) -> List[Path]:
    """Find active branch implementation plans or candidate plans to audit."""
    plans: List[Path] = []
    is_git = (repo_root / ".git").exists() or (repo_root / ".git").is_file()
    if not is_git or not (repo_root / "AGENTS.md").is_file():
        found = list(repo_root.glob("plans/**/implementation_plan.md")) + list(repo_root.glob("implementation_plan.md"))
        return sorted(found)

    branch = ""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        branch = res.stdout.strip()
    except Exception:
        pass

    if branch and branch != "main":
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", branch).strip("_").lower()
        bp = repo_root / "plans" / slug / "implementation_plan.md"
        if bp.is_file():
            plans.append(bp)

    for cmd in (["git", "diff", "--name-only", "main...HEAD"], ["git", "status", "--porcelain"]):
        try:
            res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    clean = line[3:].strip() if cmd[1] == "status" else line.strip()
                    if clean.endswith("implementation_plan.md") or (
                        clean.endswith(".md")
                        and "plan" in clean.lower()
                        and not clean.startswith(("output/", ".agents/", "tests/", "archive/", "docs/archive/"))
                        and "source" not in clean.lower()
                        and not clean.endswith("README.md")
                    ):
                        p = repo_root / clean
                        if p.is_file() and p not in plans:
                            plans.append(p)
        except Exception:
            pass

    return sorted(plans)


def audit_candidate_file(
    cand: Path,
    repo_root: Path,
    validate_func: Callable[[str, str], Tuple[bool, List[str], List[Dict[str, Any]]]],
) -> Tuple[bool, List[str]]:
    """Audit a markdown summary or plan file for wave completion and verified receipts."""
    rel = cand.relative_to(repo_root) if cand.is_relative_to(repo_root) else cand
    c_text = cand.read_text(encoding="utf-8")
    is_comp, errs, recs = validate_func(c_text, cand.name)
    if not is_comp:
        return False, [f"{rel}: {e}" for e in errs]

    failures: List[str] = []
    non_complete = [r for r in recs if r["status"] != "COMPLETE"]
    for r in non_complete:
        w_id = r.get("wave_number") or r.get("wave")
        failures.append(f"{rel}: Wave {w_id} is not marked COMPLETE ({r['raw_status']})")

    for r in recs:
        rec = str(r.get("receipt", "")).strip().lower()
        w_id = r.get("wave_number") or r.get("wave")
        if not rec or any(k in rec for k in ("pending", "none", "tbd", "todo", "n/a")):
            failures.append(f"{rel}: Wave {w_id} lacks valid runtime receipt: '{r.get('receipt')}'")

    return len(failures) == 0, failures


def run_check_all(
    require_summary: bool,
    repo_root: Path,
    validate_func: Callable[[str, str], Tuple[bool, List[str], List[Dict[str, Any]]]],
) -> int:
    """Scan and validate all walkthroughs and active branch implementation plans."""
    walkthroughs = (
        list(repo_root.glob("walkthrough*.md"))
        + list(repo_root.glob("documentation/**/walkthrough*.md"))
        + list(repo_root.glob("plans/**/walkthrough*.md"))
    )
    plans = find_plan_candidates(repo_root)
    candidates = sorted(list(set(walkthroughs + plans)))
    if not candidates:
        if require_summary:
            print("❌ VALIDATION FAILED: No walkthrough or plan markdown files found to validate.", file=sys.stderr)
            return 1
        print("INFO: No walkthrough or plan markdown files found under --check-all (none validated).")
        return 0

    all_passed = True
    for cand in candidates:
        rel = cand.relative_to(repo_root) if cand.is_relative_to(repo_root) else cand
        print(f"Validating {rel}...")
        passed, errors = audit_candidate_file(cand, repo_root, validate_func)
        if not passed:
            all_passed = False
            print(f"❌ VALIDATION FAILED for {rel}: {len(errors)} error(s)")
            for e in errors:
                print(f"   - {e}")
        else:
            print(f"   ✅ {rel} valid with all waves complete and receipts verified.")

    return 0 if all_passed else 1
