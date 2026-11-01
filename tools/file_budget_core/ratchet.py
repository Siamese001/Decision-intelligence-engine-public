"""
Ratchet baseline loading and generation for file budget governance.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .mmi import count_lines

GOVERNED_DIRS = [
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
    "dual_pipeline",
    "storage",
    "scripts",
    "tools",
]


def _resolve_git_root_and_rel(path: Path) -> Tuple[Optional[Path], Optional[str]]:
    curr = path.resolve().parent
    for _ in range(8):
        if (curr / ".git").exists():
            try:
                rel = str(path.resolve().relative_to(curr)).replace("\\", "/")
                return curr, rel
            except ValueError:
                return None, None
        if curr.parent == curr:
            break
        curr = curr.parent
    return None, None


_MAIN_JSON_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}


def read_git_main_json(path: Path) -> Optional[Dict[str, Any]]:
    """Reads JSON file content from `git show main:<rel_path>` if available (cached per repo_root/rel_path)."""
    repo_root, rel_path = _resolve_git_root_and_rel(path)
    if not repo_root or not rel_path:
        return None
    cache_key = f"{repo_root}::{rel_path}"
    if cache_key in _MAIN_JSON_CACHE and repo_root == Path(__file__).resolve().parents[2]:
        return _MAIN_JSON_CACHE[cache_key]
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    }
    try:
        res = subprocess.run(
            ["git", "show", f"main:{rel_path}"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            if isinstance(data, dict):
                if repo_root == Path(__file__).resolve().parents[2]:
                    _MAIN_JSON_CACHE[cache_key] = data
                return data
    except Exception:
        pass
    if repo_root == Path(__file__).resolve().parents[2]:
        _MAIN_JSON_CACHE[cache_key] = None
    return None


def detect_numeric_map_inflation(
    local_map: Dict[str, Any], main_map: Dict[str, Any], label: str
) -> List[str]:
    """Detects upward ceiling inflation or unauthorized new baseline keys in local_map vs main_map."""
    violations: List[str] = []
    for key, local_val in local_map.items():
        try:
            lv = int(local_val)
        except (TypeError, ValueError):
            continue
        if key not in main_map:
            violations.append(
                f"{label} inflation: unauthorized new baseline entry '{key}' ({lv}) not present on main."
            )
        else:
            mv = int(main_map[key])
            if lv > mv:
                violations.append(
                    f"{label} inflation for '{key}': branch limit {lv} exceeds main ceiling {mv}."
                )
    return violations


def load_ratchet_baseline(path: Path) -> dict:
    local_data: Dict[str, Any] = {"modules": {}}
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    local_data = loaded
        except Exception:
            local_data = {"modules": {}}

    local_mods = local_data.get("modules", {}) if isinstance(local_data.get("modules"), dict) else {}
    main_data = read_git_main_json(path)
    if main_data and isinstance(main_data.get("modules"), dict):
        main_mods = dict(main_data["modules"])
        repo_root, _ = _resolve_git_root_and_rel(path)
        if repo_root:
            excl_main = read_git_main_json(repo_root / "config" / "governance" / "file_budget_exclusions.json") or {}
            for pref in (*excl_main.get("exempt_prefixes", []), *excl_main.get("cognitive_complexity_exempt_prefixes", [])):
                norm_p = str(pref).strip().replace("\\", "/")
                if norm_p.endswith(".py"):
                    fp = repo_root / norm_p
                    if fp.is_file():
                        actual_lines = count_lines(fp)
                        main_mods[norm_p] = max(int(main_mods.get(norm_p, 0) or 0), actual_lines)
                        local_mods[norm_p] = max(int(local_mods.get(norm_p, 0) or 0), actual_lines)
        inflation = detect_numeric_map_inflation(local_mods, main_mods, "File budget baseline")
        clamped_mods = {
            k: min(int(local_mods.get(k, mv)), int(mv))
            for k, mv in main_mods.items()
        }
        result = dict(local_data)
        result["modules"] = clamped_mods
        if inflation:
            result["_inflation_violations"] = inflation
        return result
    return local_data


def collect_oversized_modules(
    repo_root: Path,
    max_lines: int,
    is_excluded_fn: Callable[[str], bool],
) -> Dict[str, int]:
    oversized = {}
    for gdir in GOVERNED_DIRS:
        dp = repo_root / gdir
        if not dp.exists():
            continue
        for p in dp.rglob("*.py"):
            try:
                rel = str(p.relative_to(repo_root)).replace("\\", "/")
            except ValueError:
                rel = str(p).replace("\\", "/")
            if is_excluded_fn(rel):
                continue
            lines = count_lines(p)
            if lines > max_lines:
                oversized[rel] = lines
    return dict(sorted(oversized.items(), key=lambda x: x[1], reverse=True))


class RatchetMonotonicityViolationError(Exception):
    """Raised when an attempt is made to increase a module's baseline line budget."""


def _enforce_ratchet_bounds(
    sorted_oversized: Dict[str, int],
    existing_baseline: Dict[str, int],
    max_lines: int,
) -> Dict[str, int]:
    ratcheted_modules: Dict[str, int] = {}
    violations: list[str] = []

    for mod, lines in sorted_oversized.items():
        if mod not in existing_baseline:
            violations.append(
                f"New un-baselined module '{mod}' ({lines} lines) exceeds {max_lines}-line budget."
            )
            continue
        old_limit = existing_baseline[mod]
        if lines > old_limit:
            violations.append(
                f"Module '{mod}' grew from {old_limit} to {lines} lines (+{lines - old_limit} lines)."
            )
        else:
            ratcheted_modules[mod] = min(lines, old_limit)

    if violations:
        err_msg = "⛔ Monotonic Ratchet Violation:\n  - " + "\n  - ".join(violations)
        raise RatchetMonotonicityViolationError(err_msg)
    return ratcheted_modules


def generate_baseline(
    repo_root: Path,
    baseline_path: Path,
    max_lines: int,
    is_excluded_fn: Callable[[str], bool],
    allow_upward_override: bool = False,
) -> int:
    sorted_oversized = collect_oversized_modules(repo_root, max_lines, is_excluded_fn)
    existing_baseline = load_ratchet_baseline(baseline_path).get("modules", {})

    if existing_baseline and not allow_upward_override:
        ratcheted_modules = _enforce_ratchet_bounds(sorted_oversized, existing_baseline, max_lines)
    else:
        ratcheted_modules = sorted_oversized

    # Retain descending sort order by lines
    final_modules = dict(sorted(ratcheted_modules.items(), key=lambda x: x[1], reverse=True))

    data = {
        "schema_version": "1.0.0",
        "governance_invariant": f"AGENTS.md line-budget ratchet governance (max {max_lines} lines)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "standard_max_lines": max_lines,
        "total_oversized_modules": len(final_modules),
        "modules": final_modules,
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(
        f"✅ Generated monotonic ratchet baseline: {baseline_path} with {len(final_modules)} modules."
    )
    return 0
