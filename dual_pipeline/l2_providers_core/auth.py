"""Environment inspection and API key resolution under repository security invariants."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from config.credentials import CredentialManager
from dual_pipeline.core import ROOT, is_link_or_reparse
from dual_pipeline.l2_policy import L2Config, ProviderFailureCode

from .types import L2ProviderError


def _ignored_by_repository(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return False
    ignore_path = ROOT / ".gitignore"
    if not ignore_path.is_file():
        return False
    ignored = False
    for raw_line in ignore_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = (line[1:] if negated else line).lstrip("/")
        if pattern.endswith("/"):
            continue
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern):
            ignored = not negated
    return ignored


def _key_from_ignored_local_env(config: L2Config) -> str | None:
    path = (ROOT / config.local_env_file).absolute()
    if not path.is_file():
        return None
    if is_link_or_reparse(path):
        raise L2ProviderError(
            ProviderFailureCode.MISSING_API_KEY,
            "configured local environment file cannot be a link or reparse point",
        )
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError as exc:
        raise L2ProviderError(
            ProviderFailureCode.MISSING_API_KEY,
            "configured local environment file must remain inside the repository root",
        ) from exc
    if not _ignored_by_repository(path):
        raise L2ProviderError(
            ProviderFailureCode.MISSING_API_KEY,
            "configured local environment file is not excluded by repository ignore policy",
        )
    return CredentialManager.load_local_env_key(path, config.api_key_env)


def resolve_openai_api_key(config: L2Config) -> str:
    """Resolve the secret only during provider construction; never record it."""

    value = CredentialManager.get_credential(config.api_key_env, "").strip()
    if not value:
        import sys
        l2_mod = sys.modules.get("dual_pipeline.l2")
        key_fn = getattr(l2_mod, "_key_from_ignored_local_env", _key_from_ignored_local_env)
        value = key_fn(config) or ""
    if not value:
        raise L2ProviderError(
            ProviderFailureCode.MISSING_API_KEY,
            f"{config.api_key_env} is unavailable to the required external provider",
        )
    return value


def resolve_google_api_key(config: L2Config) -> str:
    """Resolve Google/Gemini API key under repository security invariants."""

    env_candidates: list[str] = []
    if config.api_key_env and "OPENAI" not in config.api_key_env:
        env_candidates.append(config.api_key_env)
    env_candidates.extend(["GEMINI_API_KEY", "GOOGLE_API_KEY"])

    value = ""
    for env_name in env_candidates:
        value = CredentialManager.get_credential(env_name, "").strip()
        if value:
            break

    if not value:
        from dataclasses import replace
        for env_name in env_candidates:
            sub_cfg = replace(config, api_key_env=env_name)
            val = _key_from_ignored_local_env(sub_cfg)
            if val:
                value = val
                break

    if not value:
        raise L2ProviderError(
            ProviderFailureCode.MISSING_API_KEY,
            "GEMINI_API_KEY or GOOGLE_API_KEY is unavailable to the required external provider",
        )
    return value

