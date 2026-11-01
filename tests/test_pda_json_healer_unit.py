"""Unit tests for Wave 1 Pushdown Automaton (PDA) deterministic token-stream JSON repair."""
from __future__ import annotations

import json
import time
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.validation.pydantic_gate import (
    DeterministicPDAParser,
    TokenStreamJSONHealer,
    repair_json_tokens,
)


class TestPDAJSONHealerUnit:
    """Class A unit tests verifying deterministic token stream syntax repair."""

    def test_truncated_array_closure(self) -> None:
        """Verifies unclosed array brackets are balanced cleanly."""
        raw = '{"entity_upserts": [{"id": "system:nexus"}, {"id": "system:jarvis"'
        repaired = repair_json_tokens(raw)
        data = json.loads(repaired)
        assert "entity_upserts" in data
        assert len(data["entity_upserts"]) == 2
        assert data["entity_upserts"][0]["id"] == "system:nexus"
        assert data["entity_upserts"][1]["id"] == "system:jarvis"

    def test_unterminated_string_literal(self) -> None:
        """Verifies unclosed quotes at EOF are safely closed."""
        raw = '{"title": "Executive Catchup", "notes": "Discussion regarding project arc'
        repaired = repair_json_tokens(raw)
        data = json.loads(repaired)
        assert data["title"] == "Executive Catchup"
        assert "Discussion regarding project arc" in data["notes"]

    def test_trailing_comma_before_closure(self) -> None:
        """Verifies trailing commas before closing braces/brackets are stripped."""
        raw = '{"items": [1, 2, 3, ], "meta": {"owner": "Jason", }, }'
        repaired = repair_json_tokens(raw)
        data = json.loads(repaired)
        assert data["items"] == [1, 2, 3]
        assert data["meta"]["owner"] == "Jason"

    def test_dangling_key_colon_injection(self) -> None:
        """Verifies dangling colon after a key without value receives null."""
        raw = '{"status": "active", "next_step": '
        repaired = repair_json_tokens(raw)
        data = json.loads(repaired)
        assert data["status"] == "active"
        assert data["next_step"] is None

    def test_truncated_boolean_and_null_literals(self) -> None:
        """Verifies truncated literals like tru/fal/nul are completed."""
        t1 = repair_json_tokens('{"is_active": tru')
        assert json.loads(t1)["is_active"] is True

        t2 = repair_json_tokens('{"is_pending": fal')
        assert json.loads(t2)["is_pending"] is False

        t3 = repair_json_tokens('{"optional_flag": nul')
        assert json.loads(t3)["optional_flag"] is None

    def test_markdown_fence_and_preamble_stripping(self) -> None:
        """Verifies markdown backticks and conversational preamble are stripped."""
        raw = (
            "Here is the synthesized Output 03 payload:\n"
            "```json\n"
            '{"meeting_archetype": "STRATEGIC_DISCOVERY", "entity_upserts": []}\n'
            "```"
        )
        repaired = repair_json_tokens(raw)
        data = json.loads(repaired)
        assert data["meeting_archetype"] == "STRATEGIC_DISCOVERY"
        assert data["entity_upserts"] == []

    def test_heal_output_3_integration(self) -> None:
        """Verifies DeterministicHealer.heal_output_3 parses and heals truncated JSON."""
        raw_truncated = (
            "```json\n"
            '{"entity_upserts": [{"id": "system:conduit", "name": "Conduit", "description": "API Gateway"}'
        )
        healed_str, changes = DeterministicHealer.heal_output_3(raw_truncated)
        assert any("Deterministic PDA" in c for c in changes)
        data = json.loads(healed_str)
        assert isinstance(data, dict)
        assert "entity_upserts" in data

    def test_repair_latency_under_5ms(self) -> None:
        """Asserts deterministic PDA repair completes in strictly under 5 milliseconds."""
        sample = '{"data": [{"item": ' * 20 + '123'
        t0 = time.perf_counter()
        repaired = repair_json_tokens(sample)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 5.0
        data = json.loads(repaired)
        assert isinstance(data, dict)
