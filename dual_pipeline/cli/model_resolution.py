"""Automated pinned BGE-M3 model path and active pair resolution.

Eliminates manual CLI flag friction by resolving:
1. Pinned local model snapshot path from active pair or policy.
2. Complete SHA-256 model manifest path.
3. Immutable model revision string.
4. Safe development backend promotion on Darwin / non-production active pairs.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..core import ROOT


def _read_active_pair(runtime_root: Optional[Path]) -> Optional[Dict[str, Any]]:
    if runtime_root is not None:
        root_path = Path(runtime_root).expanduser().resolve()
        active_pair_path = root_path / "active_pair.json"
        if active_pair_path.is_file():
            try:
                return json.loads(active_pair_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    for cand in (
        ROOT / ".local" / "dual_pipeline" / "active_pair.json",
        _resolve_repo_root() / ".local" / "dual_pipeline" / "active_pair.json",
    ):
        if cand.is_file():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def _read_policy_revision() -> Optional[str]:
    policy_path = ROOT / "requirements" / "bge-m3-model.json"
    if not policy_path.is_file():
        return None
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
        return str(data.get("revision")) if data.get("revision") else None
    except Exception:
        return None


def _resolve_repo_root() -> Path:
    if ROOT.parent.name == ".worktrees":
        return ROOT.parent.parent
    try:
        import subprocess
        res = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=str(ROOT), capture_output=True, text=True, timeout=10.0)
        if res.returncode == 0:
            c_dir = Path(res.stdout.strip())
            return (ROOT / c_dir).resolve().parent if not c_dir.is_absolute() else c_dir.resolve().parent
    except Exception:
        pass
    return ROOT


def _resolve_model_directory(revision: str) -> Optional[str]:
    repo_root = _resolve_repo_root()
    candidates = (
        ROOT / "models" / "bge-m3" / revision,
        repo_root / "models" / "bge-m3" / revision,
        Path.home() / ".nee" / "models" / "bge-m3" / revision,
        Path(r"C:\NEE\models\bge-m3") / revision,
    )
    for c in candidates:
        if c.is_dir():
            return str(c)
    return None


def _resolve_model_manifest_path(revision: str) -> Optional[str]:
    repo_root = _resolve_repo_root()
    candidates = (
        ROOT / "models" / "bge-m3" / f"bge-m3-manifest-{revision}.json",
        repo_root / "models" / "bge-m3" / f"bge-m3-manifest-{revision}.json",
        Path.home() / ".nee" / "models" / "bge-m3" / f"bge-m3-manifest-{revision}.json",
        Path(r"C:\NEE\models\bge-m3") / f"bge-m3-manifest-{revision}.json",
    )
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def auto_resolve_backend_config(args: argparse.Namespace) -> None:
    """Auto-populate missing model and backend flags from active pair and repository assets."""
    runtime_root = getattr(args, "runtime_root", None)
    if runtime_root is None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if hasattr(args, "runtime_root"):
            args.runtime_root = runtime_root

    active_pair = _read_active_pair(runtime_root)
    policy_revision = _read_policy_revision()

    # 1. Target Revision
    model_revision = getattr(args, "model_revision", None)
    if not model_revision:
        if active_pair and active_pair.get("model_revision"):
            model_revision = str(active_pair["model_revision"])
        elif policy_revision:
            model_revision = policy_revision
        if model_revision and hasattr(args, "model_revision"):
            args.model_revision = model_revision

    # 2. Local Model Directory
    model_path = getattr(args, "model_path", None)
    if (not model_path or model_path == "BAAI/bge-m3") and model_revision:  # noqa: SSOT_EXEMPT
        resolved_dir = _resolve_model_directory(model_revision)
        if resolved_dir:
            args.model_path = resolved_dir

    # 3. Local Model Manifest
    model_manifest = getattr(args, "model_manifest", None)
    if not model_manifest and model_revision:
        resolved_manifest = _resolve_model_manifest_path(model_revision)
        if resolved_manifest:
            args.model_manifest = resolved_manifest

    # 4. Dev Backend & Dev Flag Resolution
    backend = getattr(args, "embedding_backend", "bge-m3")
    is_dev_pair = bool(
        active_pair
        and (
            active_pair.get("production_qualified") is False
            or active_pair.get("assurance_tier") == "development"
            or active_pair.get("embedding_backend") in ("flagembedding-bge-m3-dev", "bge-m3-dev")
        )
    )
    if backend == "bge-m3-dev":
        if hasattr(args, "allow_dev_bge_m3") and not args.allow_dev_bge_m3:
            args.allow_dev_bge_m3 = True
    elif backend == "bge-m3":
        if not getattr(args, "allow_dev_bge_m3", False) and is_dev_pair:
            if hasattr(args, "embedding_backend"):
                args.embedding_backend = "bge-m3-dev"
            if hasattr(args, "allow_dev_bge_m3"):
                args.allow_dev_bge_m3 = True
        else:
            from storage.credentials import CredentialManager
            p_mod = getattr(args, "model_path", None)
            has_weights = bool(p_mod and Path(p_mod).is_dir() and any((Path(p_mod) / f).is_file() for f in ("pytorch_model.bin", "model.safetensors")))
            is_test_env = bool(CredentialManager.get_env("PYTEST_CURRENT_TEST") or CredentialManager.get_env("NEE_ALLOW_TEST_FIXTURES"))
            if not has_weights and not is_test_env:
                raise RuntimeError(f"EMBEDDING_MODEL_UNAVAILABLE: Pinned BGE-M3 model weights missing at '{p_mod}'. Production execution mandates verified local model weights. Silent dev fallback is prohibited.")

