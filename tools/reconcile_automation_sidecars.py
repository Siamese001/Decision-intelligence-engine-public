#!/usr/bin/env python3
"""tools/reconcile_automation_sidecars.py

Reconciles Antigravity host sidecars (~/.gemini/config/sidecars/*/sidecar.json)
to thin SSOT dereferencer pointers across the 6 canonical automations (with PR Queue Observer standalone),
eliminating inlined prompt sprawl, cron collisions, and retired duplicate sidecars.
Governed by contract: urn:nee:automation-prompt-link-governance:1.0.0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.task_restoration_core.ssot_pointers import (
    MAX_POINTER_WORDS,
    RETIRED_SIDECAR_DIRS,
    TASK_SIDECAR_MAP,
    generate_ssot_pointer_prompt,
    is_valid_pointer_prompt,
    resolve_primary_repo_root,
)


def compute_sha256(content: str | bytes) -> str:
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def resolve_project_id_for_repo(config_dir: Path, repo_root: Path) -> str | None:
    """Resolves the Antigravity project UUID matching the primary repository root."""
    projects_dir = config_dir / "projects"
    if not projects_dir.is_dir():
        return None
    primary_uri = f"file://{resolve_primary_repo_root(repo_root)}"
    for proj_file in sorted(projects_dir.glob("*.json")):
        try:
            pdata = json.loads(proj_file.read_text(encoding="utf-8"))
            resources = pdata.get("projectResources", {}).get("resources", [])
            for res in resources:
                folder_uri = res.get("gitFolder", {}).get("folderUri", "")
                if folder_uri.rstrip("/") == primary_uri.rstrip("/"):
                    return str(pdata.get("id") or proj_file.stem)
        except Exception:
            continue
    return None


def sync_host_config_sidecars(
    config_path: Path,
    project_id: str | None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Synchronizes canonical sidecar projectId bindings and prunes retired entries in config.json."""
    if not config_path.is_file() or not project_id:
        return {"synced": 0, "pruned": 0, "changed": False}
    try:
        raw_cfg = config_path.read_text(encoding="utf-8")
        cfg_data = json.loads(raw_cfg)
    except Exception:
        return {"synced": 0, "pruned": 0, "changed": False}

    sidecars_map = cfg_data.setdefault("sidecars", {})
    changed = False
    pruned = 0
    synced = 0

    for retired_dir in RETIRED_SIDECAR_DIRS:
        if retired_dir in sidecars_map:
            del sidecars_map[retired_dir]
            pruned += 1
            changed = True

    for info in TASK_SIDECAR_MAP.values():
        sdir = info["sidecar_dir"]
        existing = sidecars_map.get(sdir)
        if not isinstance(existing, dict):
            sidecars_map[sdir] = {"enabled": False, "projectId": project_id}
            synced += 1
            changed = True
        else:
            if existing.get("projectId") != project_id:
                existing["projectId"] = project_id
                changed = True
            if "enabled" not in existing:
                existing["enabled"] = False
                changed = True
            synced += 1

    if not dry_run:
        new_cfg = json.dumps(cfg_data, indent=2) + "\n"
        config_path.write_text(new_cfg, encoding="utf-8")
        os.utime(config_path, None)

    return {"synced": synced, "pruned": pruned, "changed": changed}


def reconcile_sidecars(
    sidecars_dir: Path,
    repo_root: Path,
    dry_run: bool = True,
    snapshot_dir: Path | None = None,
) -> dict[str, Any]:
    """Reconciles all registered automation sidecars to SSOT pointer prompts and prunes retired sidecars."""
    results: list[dict[str, Any]] = []
    retired_results: list[dict[str, Any]] = []
    snapshots: dict[str, str] = {}

    target_snapshot_dir = snapshot_dir or (repo_root / "artifacts" / "exports" / "rollback_snapshots")
    if not dry_run:
        target_snapshot_dir.mkdir(parents=True, exist_ok=True)

    # 1. Snapshot and prune retired sidecar directories
    if sidecars_dir.is_dir():
        for retired_dir_name in RETIRED_SIDECAR_DIRS:
            retired_dir = sidecars_dir / retired_dir_name
            retired_json = retired_dir / "sidecar.json"
            if retired_dir.is_dir():
                if not dry_run and retired_json.is_file():
                    snap_file = target_snapshot_dir / f"retired_{retired_dir_name}_sidecar.json"
                    snap_file.write_text(retired_json.read_text(encoding="utf-8"), encoding="utf-8")
                    snapshots[f"retired:{retired_dir_name}"] = str(snap_file)
                if not dry_run:
                    shutil.rmtree(retired_dir)
                retired_results.append({
                    "sidecar_dir": retired_dir_name,
                    "status": "PRUNED_RETIRED" if not dry_run else "PLAN_TO_PRUNE_RETIRED",
                    "path": str(retired_dir),
                })

        # Clean up rogue backup files in sidecar directories
        if not dry_run:
            for sidecar_sub in sidecars_dir.iterdir():
                if sidecar_sub.is_dir():
                    for bak_file in sidecar_sub.glob("*.bak*"):
                        try:
                            bak_file.unlink()
                        except OSError:
                            pass

    for task_id, info in TASK_SIDECAR_MAP.items():
        sidecar_dir = sidecars_dir / info["sidecar_dir"]
        sidecar_path = sidecar_dir / "sidecar.json"

        new_prompt = generate_ssot_pointer_prompt(task_id, repo_root)
        new_words = len(new_prompt.split())

        target_args = [info["cron"], "agentapi", "new-conversation"]
        extra = info.get("extra_args") or []
        target_args.extend(extra)
        target_args.extend(["--", new_prompt])

        if not sidecar_path.is_file():
            if dry_run:
                results.append({
                    "task_id": task_id,
                    "sidecar_dir": info["sidecar_dir"],
                    "status": "SKIPPED_NOT_FOUND",
                    "path": str(sidecar_path),
                })
                continue

            target_data = {
                "builtin": "schedule",
                "restart_policy": "always",
                "args": target_args,
                "display_name": info.get("title", ""),
            }
            new_content = json.dumps(target_data, indent=2) + "\n"
            new_sha = compute_sha256(new_content)

            sidecar_dir.mkdir(parents=True, exist_ok=True)
            sidecar_path.write_text(new_content, encoding="utf-8")

            results.append({
                "task_id": task_id,
                "sidecar_dir": info["sidecar_dir"],
                "status": "RECONCILED",
                "path": str(sidecar_path),
                "old_words": 0,
                "new_words": new_words,
                "old_sha256": None,
                "new_sha256": new_sha,
                "is_valid_pointer": is_valid_pointer_prompt(new_prompt),
                "pointer_prompt": new_prompt,
            })
            continue

        raw_content = sidecar_path.read_text(encoding="utf-8")
        orig_sha = compute_sha256(raw_content)

        # 1. Capture snapshot if not dry-run
        if not dry_run:
            snap_file = target_snapshot_dir / f"{task_id}_sidecar_pre_ssot.json"
            snap_file.write_text(raw_content, encoding="utf-8")
            snapshots[task_id] = str(snap_file)

        # 2. Parse existing sidecar
        data = json.loads(raw_content)
        args = data.get("args", [])
        if len(args) < 5 or args[1] != "agentapi" or args[2] != "new-conversation" or "--" not in args:
            results.append({
                "task_id": task_id,
                "sidecar_dir": info["sidecar_dir"],
                "status": "ERROR_MALFORMED_ARGS",
                "path": str(sidecar_path),
                "args_length": len(args),
            })
            continue

        prompt_idx = len(args) - 1
        old_prompt = args[prompt_idx]
        old_words = len(old_prompt.split())

        # 3. Construct updated payload
        updated_data = dict(data)
        updated_data["args"] = target_args
        if info.get("title"):
            updated_data["display_name"] = info["title"]

        new_content = json.dumps(updated_data, indent=2) + "\n"
        new_sha = compute_sha256(new_content)

        # 4. Write if not dry-run
        if not dry_run:
            sidecar_path.write_text(new_content, encoding="utf-8")

        results.append({
            "task_id": task_id,
            "sidecar_dir": info["sidecar_dir"],
            "status": "RECONCILED" if not dry_run else "PLAN_TO_RECONCILE",
            "path": str(sidecar_path),
            "old_words": old_words,
            "new_words": new_words,
            "old_sha256": orig_sha,
            "new_sha256": new_sha,
            "is_valid_pointer": is_valid_pointer_prompt(new_prompt),
            "pointer_prompt": new_prompt,
        })

    config_dir = sidecars_dir.parent
    project_id = resolve_project_id_for_repo(config_dir, repo_root)
    config_sync = sync_host_config_sidecars(
        config_path=config_dir / "config.json",
        project_id=project_id,
        dry_run=dry_run,
    )
    if not dry_run and sidecars_dir.is_dir():
        try:
            os.utime(sidecars_dir, None)
        except OSError:
            pass

    all_passed = all(
        r["status"] in ("RECONCILED", "PLAN_TO_RECONCILE") and r.get("is_valid_pointer")
        for r in results
    )

    report = {
        "contract_id": "urn:nee:automation-prompt-link-governance:1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "sidecars_scanned": len(results),
        "sidecars_reconciled": sum(1 for r in results if r["status"] in ("RECONCILED", "PLAN_TO_RECONCILE")),
        "retired_sidecars_pruned": len(retired_results),
        "retired_results": retired_results,
        "config_sync": config_sync,
        "terminal_outcome": "PASS" if all_passed and len(results) == len(TASK_SIDECAR_MAP) else "FAIL",
        "snapshots_captured": len(snapshots),
        "results": results,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Antigravity Automation Sidecar SSOT Link Reconciler")
    parser.add_argument(
        "--sidecars-dir",
        type=Path,
        default=Path.home() / ".gemini" / "config" / "sidecars",
        help="Directory containing Antigravity host sidecar directories",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Path to repository root",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check parity between sidecars and SSOT pointers without writing",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify non-mutating parity between live sidecars and canonical SSOT pointers",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute reconciliation and overwrite sidecar.json with SSOT pointers",
    )
    parser.add_argument(
        "--output-receipt",
        type=Path,
        default=None,
        help="Path to emit JSON reconciliation receipt",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    report = reconcile_sidecars(
        sidecars_dir=args.sidecars_dir,
        repo_root=args.repo_root,
        dry_run=dry_run,
    )
    if args.check:
        has_drift = any(
            r.get("old_sha256") != r.get("new_sha256") for r in report.get("results", [])
        ) or bool(report.get("retired_sidecars_pruned"))
        if has_drift:
            report["terminal_outcome"] = "FAIL"

    target_receipt = args.output_receipt
    if target_receipt is None and args.apply:
        target_receipt = ROOT / "reports" / "sidecar_ssot_reconciliation_receipt.json"

    if target_receipt is not None:
        target_receipt.parent.mkdir(parents=True, exist_ok=True)
        target_receipt.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    mode_str = "[CHECK]" if args.check else ("[DRY-RUN]" if dry_run else "[APPLIED]")
    print(f"{mode_str} Reconciled {report['sidecars_reconciled']}/{len(TASK_SIDECAR_MAP)} sidecars (pruned {report['retired_sidecars_pruned']} retired).")
    for r in report["results"]:
        print(f"  - {r['task_id']} ({r['sidecar_dir']}): {r['status']} [{r.get('old_words', 0)}w -> {r.get('new_words', 0)}w]")

    return 0 if report["terminal_outcome"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
