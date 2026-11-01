"""tests/test_ssot_link_contract.py

Lifecycle and schema verification for Antigravity Automation Prompt SSOT Link Contract.
Contract ID: urn:nee:automation-prompt-link-governance:1.0.0
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "scheduled_tasks" / "ssot_link_contract.json"
RECON_PATH = ROOT / "config" / "scheduled_tasks" / "registration_reconciliation.json"


@pytest.fixture
def ssot_contract() -> dict:
    assert CONTRACT_PATH.is_file(), f"Missing contract file at {CONTRACT_PATH}"
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def reconciliation_manifest() -> dict:
    assert RECON_PATH.is_file(), f"Missing reconciliation file at {RECON_PATH}"
    return json.loads(RECON_PATH.read_text(encoding="utf-8"))


def test_contract_schema_validity(ssot_contract: dict) -> None:
    """Verifies that the SSOT link contract itself is valid JSON Schema Draft 2020-12."""
    assert ssot_contract["$id"] == "urn:nee:automation-prompt-link-governance:1.0.0"
    assert ssot_contract["type"] == "object"
    assert "required" in ssot_contract
    assert "allow_inline_prompt_body" in ssot_contract["required"]


def test_reconciliation_tasks_conform_to_contract(ssot_contract: dict, reconciliation_manifest: dict) -> None:
    """Verifies that all 12 tasks registered in registration_reconciliation.json adhere to SSOT contract."""
    tasks = reconciliation_manifest.get("tasks", [])
    assert len(tasks) == 12, f"Expected 12 tasks, found {len(tasks)}"

    validator = jsonschema.Draft202012Validator(ssot_contract)

    for task in tasks:
        # Build payload for validation
        payload = {
            "contract_id": "urn:nee:automation-prompt-link-governance:1.0.0",
            "schema_version": "1.0.0",
            "task_id": task["task_id"],
            "canonical_ssot_path": task["canonical_prompt"],
            "ssot_link_uri": task["ssot_link_uri"],
            "dereferencer_prompt": task["dereferencer_prompt"],
            "max_pointer_words": task["max_pointer_words"],
            "allow_inline_prompt_body": task["allow_inline_prompt_body"],
        }
        errors = list(validator.iter_errors(payload))
        assert len(errors) == 0, f"Task {task['task_id']} failed contract validation: {errors}"

        # Word count check
        words = task["dereferencer_prompt"].split()
        assert len(words) < 40, f"Pointer prompt for {task['task_id']} exceeds 40 words ({len(words)} words)"

        # Target prompt file existence check
        target_file = ROOT / task["canonical_prompt"]
        assert target_file.is_file(), f"Target prompt file {target_file} does not exist"
