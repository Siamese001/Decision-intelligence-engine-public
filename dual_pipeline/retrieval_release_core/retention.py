"""Generation retention policy enforcement for the NEE runtime.

Retains:
1. The currently active pair generation.
2. The last 5 published releases.
3. Any generation explicitly referenced by an immutable receipt.

Generations outside this set are classified as eligible for archival.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from dual_pipeline.runtime_root import (
    resolve_active_pair,
    resolve_authoritative_runtime_root,
    resolve_main_repository_root,
)

logger = logging.getLogger("dual_pipeline.retrieval_release.retention")


def _get_active_generation_id(r_root: Path) -> str | None:
    try:
        pair_data, _ = resolve_active_pair(r_root)
        return pair_data.get("generation_id")
    except Exception:
        return None


def _get_release_generations(releases_dir: Path, keep_releases: int) -> set[str]:
    release_gens: set[str] = set()
    if not releases_dir.is_dir():
        return release_gens
    release_files = sorted(
        releases_dir.glob("release-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for rf in release_files[:keep_releases]:
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
            gen = data.get("generation_id") or data.get("candidate_generation_id")
            if gen:
                release_gens.add(gen)
        except Exception:
            pass
    return release_gens


def _scan_receipt_generations(scan_dirs: list[Path]) -> set[str]:
    receipt_gens: set[str] = set()
    for s_dir in scan_dirs:
        if not s_dir.is_dir():
            continue
        for rf in s_dir.rglob("*.json"):
            try:
                content = rf.read_text(encoding="utf-8")
                for token in content.split('"'):
                    if token.startswith("generation-") and len(token) > 15:
                        receipt_gens.add(token)
            except Exception:
                pass
    return receipt_gens


def _get_disk_generations(generations_dir: Path) -> set[str]:
    all_disk_gens: set[str] = set()
    if generations_dir.is_dir():
        for d in generations_dir.iterdir():
            if d.is_dir() and d.name.startswith("generation-"):
                all_disk_gens.add(d.name)
    return all_disk_gens


def evaluate_generation_retention(
    runtime_root: Path | str | None = None,
    repo_root: Path | str | None = None,
    keep_releases: int = 5,
) -> dict[str, Any]:
    """Evaluate generations under runtime root against retention criteria."""
    r_root = (
        Path(runtime_root).resolve()
        if runtime_root is not None
        else resolve_authoritative_runtime_root(repo_root)
    )
    main_repo = (
        Path(repo_root).resolve() if repo_root is not None else resolve_main_repository_root(r_root)
    )

    active_gen = _get_active_generation_id(r_root)
    release_gens = _get_release_generations(r_root / "releases", keep_releases)
    receipt_gens = _scan_receipt_generations([main_repo / "reports", r_root / "receipts"])
    all_disk_gens = _get_disk_generations(r_root / "generations")

    retained: set[str] = set()
    if active_gen:
        retained.add(active_gen)
    retained.update(release_gens)
    retained.update(receipt_gens & all_disk_gens)

    eligible = all_disk_gens - retained

    return {
        "status": "PASS",
        "runtime_root": str(r_root),
        "active_generation": active_gen,
        "release_generations": sorted(release_gens),
        "receipt_generations_count": len(receipt_gens & all_disk_gens),
        "total_generations_count": len(all_disk_gens),
        "retained_count": len(retained),
        "eligible_for_archival_count": len(eligible),
        "retained": sorted(retained),
        "eligible_for_archival": sorted(eligible),
    }


def enforce_generation_retention(
    runtime_root: Path | str | None = None,
    repo_root: Path | str | None = None,
    keep_releases: int = 5,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Enforce retention, safely moving eligible generations to archived_generations/."""
    report = evaluate_generation_retention(
        runtime_root=runtime_root,
        repo_root=repo_root,
        keep_releases=keep_releases,
    )
    r_root = Path(report["runtime_root"])
    archive_dir = r_root / "archived_generations"
    moved = []

    if not dry_run and report["eligible_for_archival"]:
        archive_dir.mkdir(parents=True, exist_ok=True)
        for gen_name in report["eligible_for_archival"]:
            src = r_root / "generations" / gen_name
            dst = archive_dir / gen_name
            if src.is_dir() and not dst.exists():
                shutil.move(str(src), str(dst))
                moved.append(gen_name)

    report["dry_run"] = dry_run
    report["moved_count"] = len(moved)
    report["moved"] = moved
    return report
