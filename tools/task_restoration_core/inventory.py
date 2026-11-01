"""tools/task_restoration_core/inventory.py

Validation of requirements inventory, source hashes, rollback recipes, and procedure defenses.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import (
    SEVEN_TASKS,
    VALID_CATEGORIES,
    VALID_DISPOSITIONS,
    VALID_TARGET_MODES,
)
from .operational import compute_file_sha256


def _validate_single_requirement(tid: str, req: dict[str, Any], errors: list[str]) -> None:
    rid = req.get("id", "")
    if not rid or not rid.startswith("REQ-"):
        errors.append(f"Task '{tid}' requirement has invalid id: {rid}")

    cat = req.get("category")
    if cat not in VALID_CATEGORIES:
        errors.append(f"Task '{tid}' requirement '{rid}' has invalid category: {cat}")

    disp = req.get("disposition")
    if disp not in VALID_DISPOSITIONS:
        errors.append(f"Task '{tid}' requirement '{rid}' has invalid disposition: {disp}")

    mode = req.get("target_mode")
    if mode not in VALID_TARGET_MODES:
        errors.append(f"Task '{tid}' requirement '{rid}' has invalid target_mode: {mode}")

    tokens = req.get("mandatory_verification_tokens", [])
    if not tokens or not isinstance(tokens, list):
        errors.append(f"Task '{tid}' requirement '{rid}' missing mandatory_verification_tokens.")


def _check_task_requirements(tasks: dict[str, Any], errors: list[str]) -> None:
    for tid, tinfo in tasks.items():
        reqs = tinfo.get("requirements", [])
        if not reqs:
            errors.append(f"Task '{tid}' has empty requirements list.")
            continue

        for req in reqs:
            _validate_single_requirement(tid, req, errors)


def _check_rollback_governance(rg: dict[str, Any], errors: list[str]) -> None:
    if rg.get("status") != "REJECTED":
        errors.append(f"Rollback governance status must be REJECTED, got: {rg.get('status')}")

    rejected_cmd = rg.get("rejected_recipe_command", "")
    if "cp docs/plans/scheduled_task_review_sources/*.md automations/prompts/" not in rejected_cmd:
        errors.append("Rollback governance must reject 'cp docs/plans/scheduled_task_review_sources/*.md automations/prompts/'")

    mappings = rg.get("exact_filename_mappings", [])
    if len(mappings) != 7:
        errors.append(f"Expected 7 exact filename mappings, got {len(mappings)}")


def _check_source_hashes(tasks: dict[str, Any], repo_root: Path, errors: list[str]) -> None:
    for tid, tinfo in tasks.items():
        hashes = tinfo.get("hashes", {})
        archive_path = repo_root / tinfo.get("archive_source_path", "")
        if archive_path.is_file():
            actual_archive_sha = compute_file_sha256(archive_path)
            expected_archive_sha = hashes.get("archive_source")
            if actual_archive_sha != expected_archive_sha:
                errors.append(f"Task '{tid}' archive hash mismatch: {actual_archive_sha} vs {expected_archive_sha}")

        prompt_path = repo_root / tinfo.get("canonical_prompt_path", "")
        if prompt_path.is_file():
            actual_prompt_sha = compute_file_sha256(prompt_path)
            expected_prompt_sha = hashes.get("current_prompt")
            if actual_prompt_sha != expected_prompt_sha:
                errors.append(f"Task '{tid}' current prompt hash mismatch: {actual_prompt_sha} vs {expected_prompt_sha}")


def verify_requirements_inventory(
    requirements_path: Path,
    repo_root: Path,
) -> tuple[bool, list[str]]:
    """Validates the seven-task requirements inventory document."""
    if not requirements_path.is_file():
        return False, [f"Requirements inventory file not found: {requirements_path}"]

    try:
        data = json.loads(requirements_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"Malformed requirements JSON: {exc}"]

    errors: list[str] = []
    if data.get("contract_id") != "urn:nee:antigravity-task-requirements:1.0.0":
        errors.append(f"Invalid contract_id: {data.get('contract_id')}")

    tasks = data.get("tasks", {})
    if set(tasks.keys()) != SEVEN_TASKS:
        missing = SEVEN_TASKS - set(tasks.keys())
        extra = set(tasks.keys()) - SEVEN_TASKS
        errors.append(f"Task set mismatch. Missing: {missing}, Extra: {extra}")

    _check_task_requirements(tasks, errors)
    _check_rollback_governance(data.get("rollback_governance", {}), errors)
    _check_source_hashes(tasks, repo_root, errors)

    return len(errors) == 0, errors


def verify_procedure_deletion_defense(
    test_prompt_text: str,
    required_tokens: list[str],
) -> tuple[bool, list[str]]:
    """Fails closed if mandatory domain procedures are missing, regardless of common tags."""
    missing = [tok for tok in required_tokens if tok not in test_prompt_text]
    if missing:
        return False, [f"Procedure deletion detected! Missing mandatory tokens: {missing}"]
    return True, []
