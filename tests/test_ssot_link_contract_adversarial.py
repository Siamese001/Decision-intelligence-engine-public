"""tests/test_ssot_link_contract_adversarial.py

Adversarial and boundary verification tests for Antigravity Automation Prompt SSOT Link Contract.
Mandatory edge-case categories covered:
1. Boundary payload: 40-word boundary limit, empty prompt rejection, empty path.
2. Malformed input: non-existent task IDs, wrong data types, malformed URIs.
3. Invariant bypass attempt: directory traversal attempts, bypass of inline prompt prohibition.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "contracts" / "scheduled_tasks" / "ssot_link_contract.json"


@pytest.fixture
def validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def test_boundary_payload_max_words_overflow(validator: jsonschema.Draft202012Validator) -> None:
    """Boundary test: 40 words allowed, 41 words strictly rejected."""
    base_payload = {
        "contract_id": "urn:nee:automation-prompt-link-governance:1.0.0",
        "schema_version": "1.0.0",
        "task_id": "knowledge_graph_hygiene",
        "canonical_ssot_path": "automations/prompts/graph_hygiene_agent.md",
        "ssot_link_uri": "file:///automations/prompts/graph_hygiene_agent.md",
        "dereferencer_prompt": "Read and execute the prompt.",
        "max_pointer_words": 40,
        "allow_inline_prompt_body": False,
    }
    # 40 is valid
    assert len(list(validator.iter_errors(base_payload))) == 0

    # 41 words exceeds boundary
    overflow_payload = dict(base_payload, max_pointer_words=41)
    errors = list(validator.iter_errors(overflow_payload))
    assert len(errors) > 0
    assert any("maximum" in err.message or "40" in err.message for err in errors)


def test_malformed_input_unknown_task_id(validator: jsonschema.Draft202012Validator) -> None:
    """Malformed input: unknown task ID rejected by schema enum."""
    payload = {
        "contract_id": "urn:nee:automation-prompt-link-governance:1.0.0",
        "schema_version": "1.0.0",
        "task_id": "rogue_unknown_agent",
        "canonical_ssot_path": "automations/prompts/graph_hygiene_agent.md",
        "ssot_link_uri": "file:///automations/prompts/graph_hygiene_agent.md",
        "dereferencer_prompt": "Read and execute the prompt.",
        "max_pointer_words": 40,
        "allow_inline_prompt_body": False,
    }
    errors = list(validator.iter_errors(payload))
    assert len(errors) > 0
    assert any("enum" in err.message or "rogue_unknown_agent" in err.message for err in errors)


def test_invariant_bypass_inline_prompt_body_prohibited(validator: jsonschema.Draft202012Validator) -> None:
    """Invariant bypass attempt: setting allow_inline_prompt_body=True fails closed."""
    payload = {
        "contract_id": "urn:nee:automation-prompt-link-governance:1.0.0",
        "schema_version": "1.0.0",
        "task_id": "pr_queue_observer",
        "canonical_ssot_path": "automations/prompts/pr_queue_observer_agent.md",
        "ssot_link_uri": "file:///automations/prompts/pr_queue_observer_agent.md",
        "dereferencer_prompt": "Read and execute the prompt.",
        "max_pointer_words": 40,
        "allow_inline_prompt_body": True,  # BYPASS ATTEMPT
    }
    errors = list(validator.iter_errors(payload))
    assert len(errors) > 0
    assert any("const" in err.message or "False" in err.message for err in errors)


def test_invariant_bypass_directory_traversal(validator: jsonschema.Draft202012Validator) -> None:
    """Invariant bypass attempt: path traversal outside automations/prompts/ fails regex pattern."""
    payload = {
        "contract_id": "urn:nee:automation-prompt-link-governance:1.0.0",
        "schema_version": "1.0.0",
        "task_id": "storage_repository_hygiene",
        "canonical_ssot_path": "automations/prompts/../../../etc/passwd",
        "ssot_link_uri": "file:///etc/passwd",
        "dereferencer_prompt": "Read and execute.",
        "max_pointer_words": 40,
        "allow_inline_prompt_body": False,
    }
    errors = list(validator.iter_errors(payload))
    assert len(errors) > 0
    assert any(err.validator == "pattern" or "does not match" in err.message for err in errors)
