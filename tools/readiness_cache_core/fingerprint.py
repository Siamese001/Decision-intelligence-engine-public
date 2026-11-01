"""Fingerprint computation for readiness qualification and stage memoization."""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .manifest import (
    InputManifest,
    _compute_env_digest,
    _get_working_tree_diff,
    _hash_file_safe,
    _resolve_tree_hash,
    build_input_manifest,
    hash_file_fast,
)


def compute_semantic_cache_key(file_path: Path) -> str:
    """Computes a semantic SHA-256 fingerprint ignoring cosmetic changes (AST for Python)."""
    if not file_path.is_file():
        return ""
    try:
        content = file_path.read_text(encoding="utf-8")
        if file_path.suffix == ".py":
            try:
                tree = ast.parse(content)
                normalized = ast.dump(tree)
            except SyntaxError:
                normalized = content
        else:
            normalized = "\n".join(line.rstrip() for line in content.splitlines()).strip()

        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    except Exception:
        return ""


_IMPORT_NAMES_CACHE: dict[tuple[str, int, int], list[str]] = {}
_LOCAL_DEPS_CACHE: dict[tuple[str, int, int], list[Path]] = {}


def _resolve_local_module(module: str, repo_root: Path) -> list[Path]:
    """Resolve a Python import to repository files, ignoring installed packages."""
    if not module:
        return []
    base = repo_root.joinpath(*module.split("."))
    candidates = [base.with_suffix(".py"), base / "__init__.py"]
    return [path for path in candidates if path.is_file()]


def _read_import_names(source: Path, repo_root: Optional[Path] = None) -> list[str]:
    """Return absolute and resolved relative import names from a Python source file."""
    if not source.is_file():
        return []
    try:
        st = source.stat()
        cache_key = (str(source.resolve()), st.st_mtime_ns, st.st_size)
        if cache_key in _IMPORT_NAMES_CACHE:
            return _IMPORT_NAMES_CACHE[cache_key]
    except Exception:
        cache_key = None

    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except Exception:
        return []

    pkg_parts: tuple[str, ...] = ()
    if repo_root is not None:
        try:
            rel = source.resolve().relative_to(repo_root.resolve())
            pkg_parts = rel.parent.parts
        except Exception:
            pass

    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.append(node.module)
            elif node.level > 0:
                up = max(0, len(pkg_parts) - (node.level - 1))
                base_pkg = ".".join(pkg_parts[:up])
                if node.module:
                    full_mod = f"{base_pkg}.{node.module}" if base_pkg else node.module
                    names.append(full_mod)
                elif base_pkg:
                    for alias in node.names:
                        names.append(f"{base_pkg}.{alias.name}")

    if cache_key:
        _IMPORT_NAMES_CACHE[cache_key] = names
    return names


def _local_dependencies(entry: Path, repo_root: Path, max_files: int = 128) -> list[Path]:
    """Discover bounded transitive local imports used by a verifier."""
    if not entry.is_file():
        return []
    try:
        st = entry.stat()
        cache_key = (str(entry.resolve()), st.st_mtime_ns, st.st_size)
        if cache_key in _LOCAL_DEPS_CACHE:
            return _LOCAL_DEPS_CACHE[cache_key]
    except Exception:
        cache_key = None

    pending = [entry]
    seen: set[Path] = set()
    dependencies: set[Path] = set()
    while pending and len(dependencies) < max_files:
        source = pending.pop()
        if source in seen or not source.is_file():
            continue
        seen.add(source)
        for module in _read_import_names(source, repo_root):
            for path in _resolve_local_module(module, repo_root):
                if path in dependencies or path == entry:
                    continue
                dependencies.add(path)
                pending.append(path)
    res = sorted(dependencies)
    if cache_key:
        _LOCAL_DEPS_CACHE[cache_key] = res
    return res


def _bind_environment(hasher: Any, cmd: Sequence[str], repo_root: Path) -> None:
    """Bind runtime and gate-affecting environment without including secrets."""
    hasher.update(_compute_env_digest(repo_root).encode("utf-8"))
    if cmd:
        token = str(cmd[0])
        clean_tok = token.replace("\\", "/")
        if (
            clean_tok == sys.executable.replace("\\", "/")
            or clean_tok.endswith(("/python", "/python3", "/python.exe"))
            or "/.venv/bin/python" in clean_tok
        ):
            token = "<PYTHON>"
        hasher.update(token.encode("utf-8"))
    hasher.update(b"\x00")


def _bind_command_sources(
    hasher: Any,
    cmd: Sequence[str],
    repo_root: Path,
    hash_fn: Any,
    bind_executable: bool = False,
) -> None:
    """Bind command tokens, executable, verifier, and its local imports."""
    for token in cmd:
        normalized_token = token.replace(str(repo_root), "<REPO_ROOT>")
        clean_tok = token.replace("\\", "/")
        if (
            clean_tok == sys.executable.replace("\\", "/")
            or clean_tok.endswith(("/python", "/python3", "/python.exe"))
            or "/.venv/bin/python" in clean_tok
        ):
            normalized_token = "<PYTHON>"
        hasher.update(normalized_token.encode("utf-8"))
        hasher.update(b"\x00")
    if bind_executable and cmd:
        try:
            exe_path = Path(cmd[0])
            if exe_path.is_file():
                hasher.update(_hash_file_safe(exe_path).encode("utf-8"))
        except Exception:
            pass

    script_path: Optional[Path] = None
    # 1. Direct .py script token
    for token in cmd:
        if token.endswith(".py") and not token.startswith("-"):
            candidate = repo_root / token
            if candidate.is_file():
                script_path = candidate
                break

    # 2. Check for -m <module>
    if script_path is None:
        for idx, token in enumerate(cmd):
            if token == "-m" and idx + 1 < len(cmd):
                module_name = cmd[idx + 1]
                candidates = _resolve_local_module(module_name, repo_root)
                if candidates:
                    script_path = candidates[0]
                    break

    if script_path is None or not script_path.is_file():
        return

    hasher.update(hash_fn(script_path).encode("utf-8"))
    for dependency in _local_dependencies(script_path, repo_root):
        hasher.update(str(dependency.relative_to(repo_root)).encode("utf-8"))
        hasher.update(hash_fn(dependency).encode("utf-8"))


def _effective_targets(
    stage_name: str, cmd: Sequence[str], repo_root: Path, target_files: Sequence[str]
) -> list[str]:
    """Include the test tree when a test gate has no explicit branch targets."""
    targets = list(target_files)
    is_test_gate = "Gate 28" in stage_name or any(
        "test" in token for token in cmd if token.endswith(".py")
    )
    if targets or not is_test_gate or not (repo_root / "tests").is_dir():
        return targets
    return [str(path.relative_to(repo_root)) for path in sorted((repo_root / "tests").glob("**/*.py"))]


STAGE_DOMAIN_PATTERNS: dict[str, tuple[str, ...]] = {
    "Output Directory Hierarchy Gate": ("output/**",),
    "MCP Schema Overhead Audit": ("mcp/**", "contracts/mcp/**"),
    "Workspace Skills Budget & Token Overhead Gate": (".agents/skills/**", "config/skills/**"),
    "HITL Ledger Merkle Integrity": ("hitl_decision_ledger.json", "reports/hitl/**"),
    "Dual Pipeline Contracts Validation": ("contracts/**",),
    "Prompt SSOT Integrity Linter": ("prompts/**", "dual_pipeline/prompts/**"),
    "Wave Implementation Plan Schema Gate": ("docs/plans/**", "documentation/**"),
    "Wave Implementation Status Table Linter": ("docs/plans/**", "documentation/**"),
    "Strict Type Gate": ("dual_pipeline/**", "storage/**", "tools/**"),
    "Epistemic Boundaries Audit": ("dual_pipeline/**", "storage/**", "contracts/**"),
    "Runtime Evidence Controls": ("dual_pipeline/**", "contracts/**"),
    "Raw SQLite Direct Usage Linter (Ruff TID251)": ("dual_pipeline/**", "storage/**", "tools/**"),
    "Blast Radius Risk Linter": ("config/**", "governance/**"),
    "SSOT Configuration & Release Threshold Scanner": ("config/**", "governance/**", "pyproject.toml"),
    "Model-Neutral Artifact Naming": ("dual_pipeline/**", "storage/**", "tools/**", "docs/**"),
    "Copilot Active Graph Smoke Gate": ("dual_pipeline/**", "storage/**", "config/**"),
    "Gate 31: Copilot Active Graph Smoke Gate": ("dual_pipeline/**", "storage/**", "config/**"),
    "Workspace Rules Budget & >90% Deduplication Gate": ("AGENTS.md", ".agents/rules/**", "governance/**"),
    "Grounded Delivery & Claim Manifest Verification": ("dual_pipeline/grounding/**", "output/**", "reports/**"),
    "Gate 32: Direct Graph Write Path Linter": ("dual_pipeline/**", "storage/**", "tools/**"),
    "Test & Healer Ratchet Linter": ("tests/**", "tools/**", "config/**"),
    "Gate 10: E2E Coverage Audit Score (Dimension 2 ≥ 80%)": ("tests/**", "tools/audit_testing_gaps.py", "reports/testing_gaps/**"),
    "Core Architectural & Governance Test Suite": ("tests/**", "storage/**", "dual_pipeline/**", "tools/**"),
}


def _collect_domain_files(repo_root: Path, patterns: Sequence[str]) -> list[str]:
    """Collect existing files matching domain glob patterns."""
    matched: set[str] = set()
    for pat in patterns:
        glob_pat = pat.replace("/**", "/**/*") if "/**" in pat else pat
        p = repo_root / pat
        if p.is_file():
            matched.add(str(p.relative_to(repo_root)))
        elif p.is_dir():
            for sub in p.rglob("*"):
                if sub.is_file():
                    matched.add(str(sub.relative_to(repo_root)))
        else:
            for match in repo_root.glob(glob_pat):
                if match.is_file():
                    matched.add(str(match.relative_to(repo_root)))
    return sorted(matched)


def _bind_candidate_inputs(
    hasher: Any,
    cmd: Sequence[str],
    repo_root: Path,
    tree_hash: str,
    targets: Sequence[str],
    hash_fn: Any,
    stage_name: str = "",
) -> None:
    """Bind file-scoped inputs, domain-specific candidate inputs, or the complete repository candidate."""
    is_file_scoped = any(target in cmd for target in targets) or "--staged" in cmd
    if is_file_scoped and targets:
        for target in sorted(targets):
            hasher.update(target.encode("utf-8"))
            hasher.update(hash_fn(repo_root / target).encode("utf-8"))
        hasher.update(_get_working_tree_diff(repo_root, targets).encode("utf-8"))
        return

    domain_patterns = STAGE_DOMAIN_PATTERNS.get(stage_name)
    if domain_patterns:
        domain_files = _collect_domain_files(repo_root, domain_patterns)
        for df in domain_files:
            hasher.update(df.encode("utf-8"))
            hasher.update(hash_fn(repo_root / df).encode("utf-8"))
        hasher.update(_get_working_tree_diff(repo_root, domain_files).encode("utf-8"))
        return

    hasher.update(tree_hash.encode("utf-8"))
    hasher.update(_get_working_tree_diff(repo_root).encode("utf-8"))


def compute_stage_fingerprint(
    stage_name: str,
    cmd: Sequence[str],
    repo_root: Path,
    head_sha: str,
    target_files: Sequence[str] = (),
    use_semantic_cache: bool = False,
    manifest: Optional[InputManifest] = None,
) -> str:
    """Computes a content-addressable SHA-256 fingerprint for a readiness stage.

    When an immutable InputManifest is supplied, derivation reuses precomputed
    manifest state while isolating file-scoped and domain-bounded stages from unrelated repo changes.
    """
    hasher = hashlib.sha256()
    hasher.update(stage_name.encode("utf-8"))
    hasher.update(b"\x00")
    hash_fn = compute_semantic_cache_key if use_semantic_cache else _hash_file_safe
    if not use_semantic_cache and manifest is not None and getattr(manifest, "tree_snapshot", None) is not None:
        snap = manifest.tree_snapshot
        hash_fn = lambda p: hash_file_fast(p, repo_root, snap)

    targets = _effective_targets(stage_name, cmd, repo_root, target_files)
    is_file_scoped = (any(target in cmd for target in targets) or "--staged" in cmd) and bool(targets)
    domain_patterns = STAGE_DOMAIN_PATTERNS.get(stage_name)

    if manifest is not None and not is_file_scoped and not domain_patterns:
        hasher.update(manifest.manifest_digest.encode("utf-8"))
        _bind_command_sources(hasher, cmd, repo_root, hash_fn)
        return hasher.hexdigest()

    _bind_environment(hasher, cmd, repo_root)
    _bind_command_sources(hasher, cmd, repo_root, hash_fn)
    tree_hash = manifest.tree_hash if manifest is not None else _resolve_tree_hash(repo_root, head_sha)
    _bind_candidate_inputs(hasher, cmd, repo_root, tree_hash, targets, hash_fn, stage_name=stage_name)
    return hasher.hexdigest()


def compute_qualification_fingerprint(
    stages: Sequence[tuple[str, Sequence[str]]],
    repo_root: Path,
    head_sha: str,
    target_files: Sequence[str] = (),
    use_semantic_cache: bool = False,
    manifest: Optional[InputManifest] = None,
) -> str:
    """Bind a whole readiness qualification result to every stage, input, dependency, and environment."""
    hasher = hashlib.sha256(b"readiness-qualification-v3\x00")
    hash_fn = compute_semantic_cache_key if use_semantic_cache else _hash_file_safe

    if manifest is not None:
        hasher.update(manifest.manifest_digest.encode("utf-8"))
        for stage_name, cmd in stages:
            hasher.update(stage_name.encode("utf-8"))
            _bind_command_sources(hasher, cmd, repo_root, hash_fn, bind_executable=True)
        return hasher.hexdigest()

    _bind_environment(hasher, ("qualification",), repo_root)
    hasher.update(_resolve_tree_hash(repo_root, head_sha).encode("utf-8"))
    hasher.update(_get_working_tree_diff(repo_root).encode("utf-8"))
    for target in sorted(target_files):
        hasher.update(target.encode("utf-8"))
        hasher.update(hash_fn(repo_root / target).encode("utf-8"))
    for stage_name, cmd in stages:
        hasher.update(stage_name.encode("utf-8"))
        _bind_command_sources(hasher, cmd, repo_root, hash_fn, bind_executable=True)
    return hasher.hexdigest()
