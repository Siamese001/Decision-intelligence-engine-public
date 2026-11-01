"""tools/task_restoration_core/ssot_pointers.py

Single Source of Truth (SSOT) pointer utilities and validation for Antigravity automations.
Governed by contract: urn:nee:automation-prompt-link-governance:1.0.0
Loads canonical task definitions directly from config/scheduled_tasks/task_registry.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

MAX_POINTER_WORDS = 40


def resolve_primary_repo_root(repo_root: Path | None = None) -> Path:
    """Resolves the canonical primary repository root even when inside a linked worktree."""
    target = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    git_file = target / ".git"
    if git_file.is_file():
        try:
            txt = git_file.read_text(encoding="utf-8").strip()
            if txt.startswith("gitdir:"):
                gdir = Path(txt.split("gitdir:", 1)[1].strip()).resolve()
                if gdir.parent.name == "worktrees" and gdir.parent.parent.name == ".git":
                    return gdir.parent.parent.parent
        except Exception:
            pass
    parts = target.parts
    if ".worktrees" in parts:
        idx = parts.index(".worktrees")
        return Path(*parts[:idx])
    return target


def _load_registry_ssot(repo_root: Path | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, str], tuple[str, ...]]:
    """Loads TASK_SIDECAR_MAP, CONSOLIDATED_TASK_ALIASES, and RETIRED_SIDECAR_DIRS from task_registry.json."""
    local_root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    reg_path = local_root / "config" / "scheduled_tasks" / "task_registry.json"
    if not reg_path.is_file():
        root = resolve_primary_repo_root(repo_root)
        reg_path = root / "config" / "scheduled_tasks" / "task_registry.json"
    data = json.loads(reg_path.read_text(encoding="utf-8"))
    sidecar_map: dict[str, dict[str, Any]] = dict(data.get("sidecar_map") or {})
    aliases: dict[str, str] = dict(data.get("consolidated_task_aliases") or {})
    retired: tuple[str, ...] = tuple(data.get("retired_sidecar_dirs") or ())
    return sidecar_map, aliases, retired


TASK_SIDECAR_MAP, CONSOLIDATED_TASK_ALIASES, RETIRED_SIDECAR_DIRS = _load_registry_ssot()

POINTER_TEMPLATE = (
    "Read and execute the task specification defined at "
    "file://{abs_path} "
    "following all rules and invariants within."
)

POINTER_REGEX = re.compile(
    r"^Read and execute the task specification defined at (file://[^\s]+) following all rules and invariants within\.$"
)


def sanitize_reconciliation_manifest(manifest: dict[str, Any], repo_root: Path | None = None) -> dict[str, Any]:
    """Strips volatile timestamps and normalizes host-specific paths in registration_reconciliation.json."""
    root = resolve_primary_repo_root(repo_root)
    cleaned = json.loads(json.dumps(manifest))
    cleaned.pop("generated_at", None)
    for task in cleaned.get("tasks", []):
        if not isinstance(task, dict):
            continue
        canon_rel = str(task.get("canonical_prompt", "")).strip()
        if canon_rel:
            abs_p = (root / canon_rel).resolve()
            task["ssot_link_uri"] = f"file://{abs_p}"
            task["dereferencer_prompt"] = POINTER_TEMPLATE.format(abs_path=abs_p)
    return cleaned


def resolve_canonical_task_id(task_id: str) -> str:
    """Resolves a task_id or legacy consolidated alias to its canonical MECE task_id."""
    if task_id in TASK_SIDECAR_MAP:
        return task_id
    if task_id in CONSOLIDATED_TASK_ALIASES:
        return CONSOLIDATED_TASK_ALIASES[task_id]
    raise ValueError(f"Unknown task_id '{task_id}'. Must be one of {list(TASK_SIDECAR_MAP.keys())}")


def generate_ssot_pointer_prompt(task_id: str, repo_root: Path | None = None) -> str:
    """Generates the canonical thin dereferencer prompt for a given task (supports aliases)."""
    canon_id = resolve_canonical_task_id(task_id)
    canonical_rel = TASK_SIDECAR_MAP[canon_id]["canonical_prompt"]
    primary_root = resolve_primary_repo_root(repo_root)
    abs_path = (primary_root / canonical_rel).resolve()
    prompt = POINTER_TEMPLATE.format(abs_path=abs_path)

    words = prompt.split()
    if len(words) > MAX_POINTER_WORDS:
        raise ValueError(f"Generated prompt exceeds {MAX_POINTER_WORDS} words ({len(words)} words): {prompt}")

    return prompt


def is_valid_pointer_prompt(prompt_text: str) -> bool:
    """Validates whether a prompt string is a compliant thin SSOT dereferencer."""
    if not prompt_text:
        return False
    words = prompt_text.split()
    if len(words) > MAX_POINTER_WORDS:
        return False

    match = POINTER_REGEX.match(prompt_text.strip())
    if not match:
        return False

    target_uri = match.group(1)
    if "automations/prompts/" not in target_uri:
        return False

    return True


def extract_target_path(prompt_text: str) -> Optional[Path]:
    """Extracts the local file path targeted by a valid pointer prompt."""
    match = POINTER_REGEX.match(prompt_text.strip())
    if not match:
        return None
    uri = match.group(1)
    if uri.startswith("file://"):
        return Path(uri[7:])
    return Path(uri)
