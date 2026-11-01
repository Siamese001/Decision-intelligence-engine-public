"""Immutable Input Manifest and Pre-Seal Revalidation for Readiness Qualification."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from .constants import (
    CONFIG_FILES,
    ENV_VARS_TO_BIND,
    PACKAGES_TO_BIND,
    QUALITY_INPUT_PATTERNS,
    TEST_HELPER_PATTERNS,
    is_generated_report,
)


@dataclass(frozen=True)
class GitTreeSnapshot:
    """In-memory index of tracked blob SHAs and dirty working-tree files under GIT_OPTIONAL_LOCKS=0."""

    blob_map: Dict[str, str]
    dirty_files: Dict[str, str]


@dataclass(frozen=True)
class InputManifest:
    """Immutable input manifest capturing all candidate and environmental inputs for a qualification run."""

    head_sha: str
    tree_hash: str
    working_tree_diff_hash: str
    environment_digest: str
    config_digests: Dict[str, str]
    quality_inputs_digests: Dict[str, str]
    dependencies_digests: Dict[str, str]
    target_files_digests: Dict[str, str]
    created_at_utc: str
    manifest_digest: str
    tree_snapshot: Optional[GitTreeSnapshot] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("tree_snapshot", None)
        return d


def capture_git_tree_snapshot(repo_root: Path) -> GitTreeSnapshot:
    """Single-pass snapshot of tracked blob SHAs and dirty working-tree files under GIT_OPTIONAL_LOCKS=0."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    blob_map: Dict[str, str] = {}
    try:
        ls_proc = subprocess.run(
            ["git", "ls-files", "-s"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
            check=False,
        )
        for line in ls_proc.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                meta = parts[0].split()
                if len(meta) >= 2:
                    blob_map[parts[1]] = meta[1]
    except Exception:
        pass

    dirty_files: Dict[str, str] = {}
    try:
        st_proc = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=repo_root,
            capture_output=True,
            env=env,
            timeout=5,
            check=False,
        )
        entries = st_proc.stdout.split(b"\x00")
        i = 0
        while i < len(entries):
            entry = entries[i]
            if not entry or len(entry) < 4:
                i += 1
                continue
            status_xy = entry[:2].decode("utf-8", errors="replace")
            rel_path = entry[3:].decode("utf-8", errors="replace")
            if "R" in status_xy or "C" in status_xy:
                i += 1
                if i < len(entries):
                    rel_path = entries[i].decode("utf-8", errors="replace")
            if is_generated_report(rel_path):
                i += 1
                continue
            abs_p = repo_root / rel_path
            if abs_p.is_file():
                dirty_files[rel_path] = _hash_file_safe(abs_p)
            else:
                dirty_files[rel_path] = "DELETED"
            i += 1
    except Exception:
        pass

    return GitTreeSnapshot(blob_map=blob_map, dirty_files=dirty_files)


def hash_file_fast(file_path: Path, repo_root: Path, snapshot: Optional[GitTreeSnapshot] = None) -> str:
    """Return file digest using fast git tree snapshot if available, falling back to disk read."""
    if snapshot is not None:
        try:
            rel = str(file_path.relative_to(repo_root))
            if rel in snapshot.dirty_files:
                val = snapshot.dirty_files[rel]
                return "" if val == "DELETED" else val
            if rel in snapshot.blob_map:
                return snapshot.blob_map[rel]
        except Exception:
            pass
    return _hash_file_safe(file_path)


def _hash_file_safe(file_path: Path) -> str:
    """Computes SHA-256 of file contents or returns empty string if missing."""
    if not file_path.is_file():
        return ""
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _compute_env_digest(repo_root: Path) -> str:
    """Computes deterministic hash of system environment, python packages, bound env vars, and UTC date."""
    h = hashlib.sha256()
    utc_date = os.environ.get(
        "NEE_READINESS_UTC_DATE",
        dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
    )
    env_tokens = [
        sys.version,
        sys.implementation.cache_tag or "",
        platform.system(),
        platform.release(),
        platform.machine(),
        "<PYTHON>",
        f"UTC_DATE={utc_date}",
    ]
    for var in ENV_VARS_TO_BIND:
        env_tokens.append(f"{var}={os.environ.get(var, '')}")
    for pkg in PACKAGES_TO_BIND:
        try:
            ver = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            ver = "missing"
        env_tokens.append(f"{pkg}={ver}")
    for cfg in CONFIG_FILES:
        cfg_p = repo_root / cfg
        if cfg_p.is_file():
            env_tokens.append(f"CFG:{cfg}={_hash_file_safe(cfg_p)}")

    for token in env_tokens:
        h.update(token.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _get_working_tree_diff(repo_root: Path, target_files: Sequence[str] = ()) -> str:
    """Hash staged, unstaged, and untracked inputs, excluding generated reports and including quality inputs."""
    hasher = hashlib.sha256()
    pathspec = list(target_files) or [
        ".",
        ":(exclude).local",
        ":(exclude)reports/pr_latency",
        ":(exclude)reports/pr_review_*",
        ":(exclude)reports/pr_merge_*",
        ":(exclude)reports/pr_promotion_*",
        ":(exclude)reports/readiness",
        ":(exclude)reports/local_main_readiness_*",
    ]
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        for cached in (False, True):
            cmd = ["git", "diff", "--binary"]
            if cached:
                cmd.append("--cached")
            cmd.extend(["--", *pathspec])
            res = subprocess.run(cmd, timeout=5, cwd=repo_root, capture_output=True, env=env, check=False)
            hasher.update(res.stdout)
        status_cmd = ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"]
        status_run = subprocess.run(status_cmd, timeout=5, cwd=repo_root, capture_output=True, env=env, check=False)
        status = status_run.stdout.decode("utf-8", errors="replace")
        for item in status.split("\0"):
            if not item or not item.startswith("?? "):
                continue
            rel = item[3:]
            if is_generated_report(rel):
                continue
            if target_files and rel not in target_files:
                continue
            hasher.update(rel.encode("utf-8"))
            hasher.update(_hash_file_safe(repo_root / rel).encode("utf-8"))
    except Exception:
        return ""
    return hasher.hexdigest()


def _resolve_tree_hash(repo_root: Path, head_sha: str) -> str:
    """Resolve a stable tree identity, falling back to the supplied commit identity."""
    try:
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
        tree_cmd = ["git", "rev-parse", f"{head_sha}^{{tree}}"]
        result = subprocess.run(tree_cmd, timeout=5, cwd=repo_root, capture_output=True, text=True, env=env, check=False)
        return result.stdout.strip() or head_sha
    except Exception:
        return head_sha


def _collect_quality_inputs_digests(
    repo_root: Path, snapshot: Optional[GitTreeSnapshot] = None
) -> Dict[str, str]:
    """Hashes all files that serve as quality inputs (e.g., alignment receipts, manifests)."""
    digests: Dict[str, str] = {}
    for pattern in QUALITY_INPUT_PATTERNS:
        base_dir = repo_root / pattern
        if base_dir.is_dir():
            for p in sorted(base_dir.rglob("*.json")):
                rel = str(p.relative_to(repo_root))
                digests[rel] = hash_file_fast(p, repo_root, snapshot)
        elif pattern.endswith(".json") and (repo_root / pattern).is_file():
            digests[pattern] = hash_file_fast(repo_root / pattern, repo_root, snapshot)
        else:
            # Handle glob patterns like REPO_TESTING_GAPS_AUDIT_REPORT*
            parent = (repo_root / pattern).parent
            prefix = (repo_root / pattern).name
            if parent.is_dir():
                for p in sorted(parent.glob(f"{prefix}*")):
                    if p.is_file():
                        rel = str(p.relative_to(repo_root))
                        digests[rel] = hash_file_fast(p, repo_root, snapshot)
    return digests


def _collect_config_digests(
    repo_root: Path, snapshot: Optional[GitTreeSnapshot] = None
) -> Dict[str, str]:
    """Hashes governed configuration files."""
    digests: Dict[str, str] = {}
    for rel in CONFIG_FILES:
        p = repo_root / rel
        if p.is_file():
            digests[rel] = hash_file_fast(p, repo_root, snapshot)
    return digests


def _collect_dependencies_digests(
    repo_root: Path, snapshot: Optional[GitTreeSnapshot] = None
) -> Dict[str, str]:
    """Hashes test helpers and verifier dependencies."""
    digests: Dict[str, str] = {}
    for rel in TEST_HELPER_PATTERNS:
        p = repo_root / rel
        if p.is_file():
            digests[rel] = hash_file_fast(p, repo_root, snapshot)
    return digests


def build_input_manifest(
    repo_root: Path,
    head_sha: str,
    target_files: Sequence[str] = (),
    use_semantic_cache: bool = False,
) -> InputManifest:
    """Constructs an immutable InputManifest capturing all candidate inputs and environment."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        resolved_head = subprocess.run(
            ["git", "rev-parse", head_sha],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        ).stdout.strip()
    except Exception:
        resolved_head = head_sha
    head_sha = resolved_head
    tree_hash = _resolve_tree_hash(repo_root, head_sha)
    snapshot = capture_git_tree_snapshot(repo_root)
    diff_hash = _get_working_tree_diff(repo_root, target_files)
    env_digest = _compute_env_digest(repo_root)
    config_digests = _collect_config_digests(repo_root, snapshot)
    quality_inputs_digests = _collect_quality_inputs_digests(repo_root, snapshot)
    deps_digests = _collect_dependencies_digests(repo_root, snapshot)

    targets_digests: Dict[str, str] = {}
    for t in sorted(target_files):
        p = repo_root / t
        targets_digests[t] = hash_file_fast(p, repo_root, snapshot)

    created_at = dt.datetime.now(dt.timezone.utc).isoformat()

    manifest_payload = {
        "tree_hash": tree_hash,
        "working_tree_diff_hash": diff_hash,
        "environment_digest": env_digest,
        "config_digests": config_digests,
        "quality_inputs_digests": quality_inputs_digests,
        "dependencies_digests": deps_digests,
        "target_files_digests": targets_digests,
    }
    canonical_bytes = json.dumps(manifest_payload, sort_keys=True).encode("utf-8")
    manifest_digest = hashlib.sha256(canonical_bytes).hexdigest()

    return InputManifest(
        head_sha=head_sha,
        tree_hash=tree_hash,
        working_tree_diff_hash=diff_hash,
        environment_digest=env_digest,
        config_digests=config_digests,
        quality_inputs_digests=quality_inputs_digests,
        dependencies_digests=deps_digests,
        target_files_digests=targets_digests,
        created_at_utc=created_at,
        manifest_digest=manifest_digest,
        tree_snapshot=snapshot,
    )


def validate_manifest_freshness(manifest: InputManifest, repo_root: Path) -> Tuple[bool, str]:
    """Pre-seal revalidation: verifies that workspace inputs and environment have not mutated mid-run.

    Returns (is_fresh, reason).
    """
    # 1. Check current HEAD commit
    try:
        curr_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
        ).stdout.strip()
        if curr_head != manifest.head_sha:
            return False, f"HEAD commit changed mid-run (expected {manifest.head_sha[:8]}, now {curr_head[:8]})"
    except Exception as e:
        return False, f"Git rev-parse HEAD failed: {e}"

    # 2. Check working tree diff
    target_files = list(manifest.target_files_digests.keys())
    curr_diff = _get_working_tree_diff(repo_root, target_files)
    if curr_diff != manifest.working_tree_diff_hash:
        return False, "Working tree diff modified mid-run"

    # 3. Check configs and inputs with fresh tree snapshot
    fresh_snapshot = capture_git_tree_snapshot(repo_root)
    curr_configs = _collect_config_digests(repo_root, fresh_snapshot)
    if curr_configs != manifest.config_digests:
        return False, "Governed configuration files modified mid-run"

    # 4. Check quality inputs
    curr_quality = _collect_quality_inputs_digests(repo_root, fresh_snapshot)
    if curr_quality != manifest.quality_inputs_digests:
        return False, "Quality input reports modified mid-run"

    # 5. Check target files
    for t, expected_digest in manifest.target_files_digests.items():
        curr_digest = hash_file_fast(repo_root / t, repo_root, fresh_snapshot)
        if curr_digest != expected_digest:
            return False, f"Target file '{t}' modified mid-run"

    return True, "Manifest fresh and verified"
