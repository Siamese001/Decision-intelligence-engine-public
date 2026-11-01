#!/usr/bin/env python3
"""
tools/sync_env_ssot.py

Central Workstation AI Environment Synchronization and Drift Detection Engine.
Enforces the Hierarchical Layered SSOT Architecture:
1. Central Workstation SSOT (~/.config/ai_env/common.env) holds machine-wide shared credentials.
2. Project Environment File (env_di) holds project-specific domain overrides.
3. Manages bidirectional drift detection, rotation propagation, and governance verification.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CENTRAL_SSOT_FILE = Path.home() / ".config" / "ai_env" / "common.env"
FALLBACK_SSOT_FILE = Path.home() / "env" / "common.env"
ENV_EXAMPLE_FILE = ROOT / ".env.example"

SHARED_KEY_NAMES = [
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GITHUB_TOKEN",
    "HF_TOKEN",
]

PROHIBITED_IN_COMMON = [
    "OPENAI_API_KEY",
]


def verify_env_example_template(example_file: Path = ENV_EXAMPLE_FILE) -> Tuple[bool, List[str]]:
    """Validates that the tracked `.env.example` template exists and declares all governed SSOT keys."""
    if not example_file.is_file():
        return False, [f"Tracked environment template missing: {example_file}"]
    vals = dotenv_values(example_file)
    missing = [k for k in [*SHARED_KEY_NAMES, "OPENAI_API_KEY"] if k not in vals]
    if missing:
        return False, [f".env.example is missing required template keys: {missing}"]
    return True, []


def resolve_central_ssot() -> Path:
    """Resolves the central SSOT file, respecting standard location or fallback."""
    if CENTRAL_SSOT_FILE.is_file():
        return CENTRAL_SSOT_FILE
    if FALLBACK_SSOT_FILE.is_file():
        return FALLBACK_SSOT_FILE.resolve()
    return CENTRAL_SSOT_FILE


def check_central_ssot_health() -> Tuple[bool, List[str]]:
    """Validates permissions and content of central workstation SSOT."""
    issues: List[str] = []
    p = resolve_central_ssot()
    if not p.is_file():
        issues.append(f"Central SSOT file not found: {p}")
        return False, issues

    # Check mode
    mode = p.stat().st_mode & 0o777
    if mode not in (0o600, 0o400):
        issues.append(f"Central SSOT file permissions insecure: {oct(mode)} (expected 0600)")

    vals = dotenv_values(p)
    for k in SHARED_KEY_NAMES:
        if k not in vals or not vals[k]:
            issues.append(f"Missing required shared key in central SSOT: {k}")

    for k in PROHIBITED_IN_COMMON:
        if k in vals:
            issues.append(f"Domain-specific key '{k}' prohibited in central SSOT (must reside in repo-specific profile).")

    return len(issues) == 0, issues


def check_drift(
    target_env_file: Path,
    expected_openai_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Compares a target repo env file against the central SSOT."""
    central_path = resolve_central_ssot()
    central_vals = dotenv_values(central_path) if central_path.is_file() else {}
    target_vals = dotenv_values(target_env_file) if target_env_file.is_file() else {}

    drift_keys: List[str] = []
    missing_keys: List[str] = []

    for k in SHARED_KEY_NAMES:
        c_val = central_vals.get(k)
        t_val = target_vals.get(k)
        if not t_val:
            missing_keys.append(k)
        elif c_val and t_val != c_val:
            drift_keys.append(k)

    openai_key = target_vals.get("OPENAI_API_KEY", "")
    has_openai = bool(openai_key)

    return {
        "target_file": str(target_env_file),
        "exists": target_env_file.is_file(),
        "has_openai_key": has_openai,
        "drift_keys": drift_keys,
        "missing_keys": missing_keys,
        "in_sync": len(drift_keys) == 0 and len(missing_keys) == 0 and has_openai,
    }


def sync_keys_to_target(target_env_file: Path) -> Tuple[bool, List[str]]:
    """Propagates shared keys from central SSOT into a target environment file, preserving existing domain keys."""
    central_path = resolve_central_ssot()
    if not central_path.is_file():
        return False, [f"Cannot sync: central SSOT file missing at {central_path}"]

    central_vals = dotenv_values(central_path)
    target_vals = dotenv_values(target_env_file) if target_env_file.is_file() else {}

    updates: List[str] = []
    merged_vals = dict(target_vals)

    for k in SHARED_KEY_NAMES:
        c_val = central_vals.get(k)
        if c_val and merged_vals.get(k) != c_val:
            merged_vals[k] = c_val
            updates.append(k)

    if not updates and target_env_file.is_file():
        return True, ["No updates needed; already in sync."]

    # Read original file lines if present to preserve ordering and comments
    new_lines: List[str] = []
    handled_keys = set()

    if target_env_file.is_file():
        for line in target_env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _ = stripped.split("=", 1)
                k = k.strip()
                if k in merged_vals:
                    new_lines.append(f"{k}={merged_vals[k]}")
                    handled_keys.add(k)
                    continue
            new_lines.append(line)

    # Append any remaining keys not in original file
    for k, v in merged_vals.items():
        if k not in handled_keys:
            new_lines.append(f"{k}={v}")

    target_env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True, [f"Synchronized keys: {', '.join(updates)}"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize and verify central workstation AI environment SSOT.")
    parser.add_argument("--check", action="store_true", help="Check SSOT health and drift across repositories.")
    parser.add_argument("--sync", action="store_true", help="Sync shared keys from central SSOT into repo env files.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    parser.add_argument("--target", type=str, help="Specific target env file to check or sync.")
    args = parser.parse_args()

    healthy, issues = check_central_ssot_health()

    from config.credentials import resolve_ssot_env_file
    target_env = resolve_ssot_env_file()
    repo_targets = [target_env]
    if args.target:
        repo_targets = [Path(args.target)]

    results: Dict[str, Any] = {
        "central_ssot": str(resolve_central_ssot()),
        "healthy": healthy,
        "issues": issues,
        "targets": {},
    }

    if args.sync:
        if not healthy:
            print(f"Error: Central SSOT is unhealthy: {issues}", file=sys.stderr)
            return 1
        for t in repo_targets:
            ok, msgs = sync_keys_to_target(t)
            results["targets"][str(t)] = {"synced": ok, "messages": msgs}

    for t in repo_targets:
        results["targets"][str(t)] = check_drift(t)

    all_in_sync = healthy and all(d.get("in_sync", False) for d in results["targets"].values())
    results["overall_in_sync"] = all_in_sync

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        status_str = "HEALTHY" if healthy else "UNHEALTHY"
        print(f"Central SSOT ({results['central_ssot']}): {status_str}")
        for iss in issues:
            print(f"  - Issue: {iss}")
        for t_path, data in results["targets"].items():
            t_name = Path(t_path).name
            status = "IN SYNC" if data.get("in_sync") else "DRIFT/MISSING"
            print(f"Target [{t_name}]: {status}")
            if data.get("drift_keys"):
                print(f"  - Drifted keys: {data['drift_keys']}")
            if data.get("missing_keys"):
                print(f"  - Missing keys: {data['missing_keys']}")

    return 0 if all_in_sync else 1


if __name__ == "__main__":
    sys.exit(main())
