"""
Data Highway Fast-Track Router & Validator for Client Deliverables.

Verifies that a branch contains exclusively client deliverables under output/,
enforces the Indivisible Meeting Triad Contract, and authorizes fast-track merge.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class DataHighwayResult:
    branch: str
    is_eligible: bool = False
    files: List[str] = field(default_factory=list)
    has_triad: bool = False
    violations: List[str] = field(default_factory=list)
    fast_track_passed: bool = False
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def verify_and_route_data_highway(
    branch: str,
    root: Path,
    base_branch: str = "main",
) -> DataHighwayResult:
    start_time = time.time()
    result = DataHighwayResult(branch=branch)

    # 1. Inspect diff files
    res = subprocess.run(
        ["git", "diff", "--name-only", f"{base_branch}...{branch}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0 or not res.stdout.strip():
        result.violations.append(f"Could not compute diff for {branch} against {base_branch}")
        result.duration_seconds = time.time() - start_time
        return result

    files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    result.files = files

    # 2. Verify all files are strictly under output/
    non_output = [f for f in files if not f.startswith("output/")]
    if non_output:
        result.violations.append(
            f"Non-deliverable files detected: {non_output[:3]} (must be strictly under output/)"
        )
        result.duration_seconds = time.time() - start_time
        return result

    # 3. Check for Production Output Purity (no draft, tmp, backup)
    purity_violations = [
        f for f in files if any(b in f.lower() for b in ("draft", "tmp", "backup"))
    ]
    if purity_violations:
        result.violations.append(
            f"Purity violation: prohibited words in filenames {purity_violations[:2]}"
        )
        result.duration_seconds = time.time() - start_time
        return result

    result.is_eligible = True

    # 4. Check for Indivisible Meeting Triad Contract (01, 02, 03)
    has_01 = any(re.search(r"01_.*\.md$", f) for f in files)
    has_02 = any(re.search(r"02_.*\.md$", f) for f in files)
    has_03 = any(re.search(r"03_.*\.json$", f) for f in files)

    if has_01 and has_02 and has_03:
        result.has_triad = True
        result.fast_track_passed = True
    else:
        missing = []
        if not has_01:
            missing.append("01_*.md (HITL Transcript)")
        if not has_02:
            missing.append("02_*.md (Executive Recap)")
        if not has_03:
            missing.append("03_*.json (Signal Projection)")
        result.violations.append(f"Triad incomplete: missing {', '.join(missing)}")

    result.duration_seconds = time.time() - start_time
    return result
