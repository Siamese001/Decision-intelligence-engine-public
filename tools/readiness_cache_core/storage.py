"""Storage and memoization retrieval for readiness stage qualification results."""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Set

from .fingerprint import compute_stage_fingerprint
from .manifest import InputManifest, validate_manifest_freshness

logger = logging.getLogger("tools.readiness_cache.storage")


@dataclass
class CachedStageResult:
    """Represents a cached successful execution of a readiness gate stage."""

    stage_name: str
    status: str
    duration_seconds: float
    command: str
    output_summary: str
    fingerprint: str
    cached_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_stage_cache_dir(repo_root: Path) -> Path:
    """Returns local cache directory for readiness stages under .local/readiness_cache."""
    try:
        from tools.coverage_report_reuse import resolve_primary_repo_root

        root = resolve_primary_repo_root(repo_root)
    except Exception:
        root = repo_root
    cache_dir = root / ".local" / "readiness_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cached_stage(
    stage_name: str,
    cmd: Sequence[str],
    repo_root: Path,
    head_sha: str,
    target_files: Sequence[str] = (),
    use_semantic_cache: bool = False,
    manifest: Optional[InputManifest] = None,
) -> CachedStageResult | None:
    """Checks whether a valid PASS cache entry exists for the given stage and inputs.

    Performs conservative fallback on corrupt receipts, missing fields, or stale manifest.
    """
    fingerprint = compute_stage_fingerprint(
        stage_name, cmd, repo_root, head_sha, target_files, use_semantic_cache, manifest=manifest
    )
    cache_file = get_stage_cache_dir(repo_root) / f"{fingerprint}.json"

    if not cache_file.is_file():
        return None

    try:
        content = cache_file.read_text(encoding="utf-8")
        data = json.loads(content)
        if not isinstance(data, dict):
            cache_file.unlink(missing_ok=True)
            return None

        # Verify required fields
        if data.get("status") != "PASS" or data.get("fingerprint") != fingerprint:
            return None

        return CachedStageResult(
            stage_name=str(data.get("stage_name", stage_name)),
            status="PASS",
            duration_seconds=float(data.get("duration_seconds", 0.001)),
            command=str(data.get("command", " ".join(cmd))),
            output_summary=str(data.get("output_summary", "⚡ Passed from cache")),
            fingerprint=fingerprint,
            cached_at=str(data.get("cached_at", "")),
        )
    except Exception as exc:
        logger.debug(f"Corrupt or invalid cache entry at {cache_file}: {exc}; evicting")
        with contextlib.suppress(Exception):
            cache_file.unlink(missing_ok=True)
        return None


def save_cached_stage(
    stage_name: str,
    cmd: Sequence[str],
    repo_root: Path,
    head_sha: str,
    output_summary: str,
    duration_seconds: float,
    target_files: Sequence[str] = (),
    use_semantic_cache: bool = False,
    manifest: Optional[InputManifest] = None,
    fingerprint: Optional[str] = None,
) -> bool:
    """Saves a successful stage run to local persistent cache."""
    stage_fp = fingerprint or compute_stage_fingerprint(
        stage_name, cmd, repo_root, head_sha, target_files, use_semantic_cache, manifest=manifest
    )
    cache_file = get_stage_cache_dir(repo_root) / f"{stage_fp}.json"

    entry = {
        "stage_name": stage_name,
        "status": "PASS",
        "duration_seconds": round(duration_seconds, 3),
        "command": " ".join(cmd),
        "output_summary": output_summary[:500] if len(output_summary) > 500 else output_summary,
        "fingerprint": stage_fp,
        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    try:
        cache_file.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.debug(f"Failed to write stage cache entry {cache_file}: {exc}")
        return False


def clear_readiness_cache(repo_root: Path) -> int:
    """Removes all cached readiness stage files."""
    cache_dir = repo_root / ".local" / "readiness_cache"
    if not cache_dir.is_dir():
        return 0
    count = 0
    for f in cache_dir.glob("*.json"):
        with contextlib.suppress(Exception):
            f.unlink()
            count += 1
    return count


def _resolve_active_repository_refs(repo_root: Path) -> tuple[set[str], set[str]]:
    """Resolves active HEAD SHAs and tree digests across main, active worktrees, and local branches."""
    active_shas: set[str] = set()
    active_trees: set[str] = set()

    with contextlib.suppress(Exception):
        res = subprocess.run(
            ["git", "for-each-ref", "--format=%(objectname) %(tree)", "refs/heads/"],
            cwd=repo_root, capture_output=True, text=True, check=False, timeout=15,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    active_shas.add(parts[0])
                    active_trees.add(parts[1])

    with contextlib.suppress(Exception):
        res = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root, capture_output=True, text=True, check=False, timeout=15,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith("HEAD "):
                    sha = line.split(" ", 1)[1].strip()
                    if sha:
                        active_shas.add(sha)
                        t_res = subprocess.run(
                            ["git", "rev-parse", f"{sha}^{{tree}}"],
                            cwd=repo_root, capture_output=True, text=True, check=False, timeout=15,
                        )
                        if t_res.returncode == 0 and t_res.stdout.strip():
                            active_trees.add(t_res.stdout.strip())

    return active_shas, active_trees


def _prune_file_cache_dir(dir_path: Path, cutoff_time: float, dry_run: bool) -> int:
    """Evicts files older than cutoff_time in dir_path."""
    if not dir_path.is_dir():
        return 0
    count = 0
    for f in dir_path.glob("*.json"):
        with contextlib.suppress(Exception):
            if f.stat().st_mtime < cutoff_time:
                if not dry_run:
                    f.unlink(missing_ok=True)
                count += 1
    return count


def _prune_tree_keyed_artifacts(
    dir_path: Path,
    preserved_trees: Set[str],
    cutoff_time: float,
    is_dir: bool,
    dry_run: bool,
) -> int:
    """Evicts items whose key is not in preserved_trees and older than cutoff_time."""
    if not dir_path.is_dir():
        return 0
    count = 0
    pattern = "*" if is_dir else "*.json"
    for item in dir_path.glob(pattern):
        key = item.name if is_dir else item.stem
        if key in preserved_trees:
            continue
        with contextlib.suppress(Exception):
            if item.stat().st_mtime < cutoff_time:
                if not dry_run:
                    if is_dir:
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                count += 1
    return count


def _prune_readiness_receipts(
    readiness_dir: Path,
    preserved_shas: Set[str],
    cutoff_time: float,
    dry_run: bool,
) -> int:
    """Evicts branch readiness receipts whose HEAD commit is not in preserved_shas and older than cutoff_time."""
    if not readiness_dir.is_dir():
        return 0
    count = 0
    for f in readiness_dir.glob("local_main_readiness_*.json"):
        if f.name == "local_main_readiness_receipt.json":
            continue
        with contextlib.suppress(Exception):
            if f.stat().st_mtime >= cutoff_time:
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
            if str(data.get("head_commit_sha", "")) in preserved_shas:
                continue
            if not dry_run:
                f.unlink(missing_ok=True)
            count += 1
    return count


def prune_stale_caches(
    repo_root: Path,
    active_tree_digests: Optional[Sequence[str]] = None,
    active_head_shas: Optional[Sequence[str]] = None,
    max_age_days: int = 7,
    dry_run: bool = False,
) -> dict[str, int]:
    """Prunes stale readiness caches, coverage packages, and expired receipts while preserving active branches."""
    try:
        from tools.coverage_report_reuse import resolve_primary_repo_root
        root = resolve_primary_repo_root(repo_root)
    except Exception:
        root = repo_root

    shas, trees = _resolve_active_repository_refs(root)
    if active_head_shas is not None:
        shas.update(active_head_shas)
    if active_tree_digests is not None:
        trees.update(active_tree_digests)

    cutoff_time = time.time() - (max_age_days * 86400.0)
    local_dir = root / ".local"

    counts: dict[str, int] = {
        "readiness_cache": _prune_file_cache_dir(local_dir / "readiness_cache", cutoff_time, dry_run),
        "test_module_cache": _prune_file_cache_dir(local_dir / "test_module_cache", cutoff_time, dry_run),
        "coverage_qualification": _prune_tree_keyed_artifacts(local_dir / "coverage_qualification", trees, cutoff_time, True, dry_run),
        "readiness_attestations": _prune_tree_keyed_artifacts(local_dir / "receipts" / "readiness", trees, cutoff_time, False, dry_run),
        "readiness_receipts": _prune_readiness_receipts(root / "reports" / "readiness", shas, cutoff_time, dry_run),
    }
    counts["total_pruned"] = sum(counts.values())
    return counts

