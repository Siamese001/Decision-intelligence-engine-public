"""
tests/adversarial/test_adversarial_json_truncation.py

Adversarial Red Team test suite for Token-Stream Pushdown Automaton (PDA) repair
and deterministic Pydantic gates (Wave 1).
Proves:
1. String literals with embedded '{' and '}' are NEVER corrupted (defeating the regex-counter flaw).
2. Truncated EOFs (unclosed strings, objects, arrays) are healed losslessly.
3. Trailing commas before closing delimiters are cleaned.
4. Validation against strict Pydantic schemas succeeds without any LLM calls.
5. Execution takes <2ms per payload.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from pydantic import BaseModel, Field

from dual_pipeline.transcript_models import EntityUpsert
from dual_pipeline.validation.pydantic_gate import (
    PydanticValidationError,
    TokenStreamJSONHealer,
    repair_json_tokens,
    validate_with_pydantic,
)


class SampleAgentAction(BaseModel):
    action_id: str
    target: str
    rationale: str
    payload: Optional[dict] = None
    subtasks: List[str] = Field(default_factory=list)


def test_adversarial_string_with_braces_not_corrupted():
    """Dr. Turing Proof: String literals containing '{' or '}' must NOT desync the PDA."""
    # This text contains curly braces inside string values.
    # A naive regex counting '{' vs '}' would see 4 '{' and 1 '}', and append 3 '}',
    # destroying the JSON grammar!
    tricky_json = (
        '{\n'
        '  "action_id": "act-101",\n'
        '  "target": "function(){ return {a: 1}; }",\n'
        '  "rationale": "Code snippet with {open and closed} braces inside string",\n'
        '  "subtasks": ["task 1", "task 2"]\n'
        '}'
    )
    healed, actions = TokenStreamJSONHealer.repair(tricky_json)
    parsed = json.loads(healed)
    assert parsed["target"] == "function(){ return {a: 1}; }"
    assert parsed["rationale"] == "Code snippet with {open and closed} braces inside string"
    assert parsed["subtasks"] == ["task 1", "task 2"]


def test_adversarial_truncated_eof_with_embedded_braces():
    """Test truncated JSON where EOF cuts off while string contains braces."""
    # Truncated right after string with brace
    truncated = (
        '{\n'
        '  "action_id": "act-102",\n'
        '  "target": "system_{env_override",\n'
        '  "rationale": "Truncated before closing object'
    )
    # The string is unclosed and the outer object is unclosed.
    healed = repair_json_tokens(truncated)
    parsed = json.loads(healed)
    assert parsed["action_id"] == "act-102"
    assert "system_{env_override" in parsed["target"]
    assert "Truncated before closing object" in parsed["rationale"]


def test_adversarial_deeply_nested_truncation():
    """Test deeply nested structures cut off at EOF."""
    nested_truncated = (
        '{"level1": [{"level2": {"level3": [{"name": "item1", "details": "cut off here'
    )
    healed = repair_json_tokens(nested_truncated)
    parsed = json.loads(healed)
    assert "level1" in parsed
    assert isinstance(parsed["level1"], list)
    assert parsed["level1"][0]["level2"]["level3"][0]["name"] == "item1"


def test_adversarial_trailing_commas():
    """Test invalid trailing commas before closing braces/brackets."""
    malformed = '{"items": [1, 2, 3, ], "config": {"key": "val", }, }'
    healed = repair_json_tokens(malformed)
    parsed = json.loads(healed)
    assert parsed["items"] == [1, 2, 3]
    assert parsed["config"] == {"key": "val"}


def test_adversarial_escaped_quotes_in_string():
    """Test strings containing escaped quotes and truncation."""
    escaped_payload = (
        '{\n'
        '  "quote": "He said \\"The project {is} on track\\" and then paused",\n'
        '  "notes": "More \\"quotes\\" here'
    )
    healed = repair_json_tokens(escaped_payload)
    parsed = json.loads(healed)
    assert 'He said \\"The project {is} on track\\"' in json.dumps(parsed["quote"])


def test_pydantic_validation_gate_success():
    """Verify strict Pydantic validation passes on healed payloads."""
    truncated_upsert = (
        '{\n'
        '  "id": "person:alex_chen",\n'
        '  "type": "PERSON",\n'
        '  "name": "Alex Chen",\n'
        '  "description": "Principal Engineer driving the distributed database migration'
    )
    # Validates against dual_pipeline.transcript_models.EntityUpsert
    upsert = validate_with_pydantic(EntityUpsert, truncated_upsert, auto_heal=True)
    assert isinstance(upsert, EntityUpsert)
    assert upsert.id == "person:alex_chen"
    assert upsert.name == "Alex Chen"
    assert "Principal Engineer" in upsert.description


def test_pydantic_validation_gate_fails_closed_on_schema_violation():
    """Verify that semantic/schema violations fail closed with PydanticValidationError."""
    # Fails because Gate 3.11 rejects generic descriptions
    invalid_upsert = {
        "id": "person:bob",
        "type": "PERSON",
        "name": "Bob",
        "description": "meeting attendee",  # generic placeholder violates Gate 3.11
    }
    try:
        validate_with_pydantic(EntityUpsert, invalid_upsert)
        raise AssertionError("Expected PydanticValidationError due to Gate 3.11 generic description")
    except PydanticValidationError as exc:
        assert "Generic placeholder description" in str(exc.errors[0]) or "Gate 3.11" in str(exc.errors[0])


def test_deterministic_pda_performance_latency():
    """Verify PDA repair completes in < 2ms without LLM invocations."""
    payload = (
        '{"evidence": [{"speaker": "Alice", "kind": "technical", "text": "Testing {latency} <2ms", '
        '"locator": {"kind": "section", "value": "Benchmark"}}, '
        '{"speaker": "Bob", "kind": "strategic", "text": "Cost is zero tokens'
    )
    start = time.perf_counter()
    for _ in range(50):
        repaired = repair_json_tokens(payload)
    elapsed_ms = ((time.perf_counter() - start) / 50.0) * 1000.0
    assert elapsed_ms < 2.0, f"PDA repair latency {elapsed_ms:.3f}ms exceeded 2ms threshold!"
    parsed = json.loads(repaired)
    assert len(parsed["evidence"]) == 2
