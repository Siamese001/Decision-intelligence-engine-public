"""
tests/end_to_end/test_adversarial_json_truncation_e2e.py

End-to-End lifecycle test verifying that the Token-Stream Pushdown Automaton (PDA)
and Deterministic Healer integrate cleanly across the pipeline without triggering
unnecessary LLM retries or failing valid truncated outputs.
"""

from __future__ import annotations

import json

from dual_pipeline.transcript_pipeline_core.format_parsers import parse_copilot_json
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer


def test_copilot_json_e2e_truncation_recovery():
    """Verify parse_copilot_json automatically heals truncated JSON streams."""
    # A Copilot JSON payload truncated mid-utterance
    truncated_copilot = (
        '[\n'
        '  {"speaker": "Alex", "timestamp": "00:01:15", "text": "Let us check the {database} cluster"},\n'
        '  {"speaker": "Sarah", "timestamp": "00:01:45", "text": "Cluster status is green'
    )
    result = parse_copilot_json(truncated_copilot)
    assert "Alex" in result
    assert "00:01:15" in result
    assert "Sarah" in result
    assert "Cluster status is green" in result


def test_healing_mesh_output3_e2e_recovery():
    """Verify DeterministicHealer.heal_output_3 recovers truncated graph payloads."""
    truncated_output3 = (
        '{\n'
        '  "meeting_id": "test-meeting-101",\n'
        '  "graph_mutations": {\n'
        '    "entity_upserts": [\n'
        '      {"id": "system:jarvis", "type": "SYSTEM", "name": "JARVIS Core", "description": "Core orchestration"}\n'
        '    ]\n'
        '  }\n'
        '}'
    )
    # Intentionally cut off before closing outermost brace
    cut_off = truncated_output3[:-2]
    healed_str, changes = DeterministicHealer.heal_output_3(cut_off)
    assert any("Deterministic PDA" in c for c in changes)
    data = json.loads(healed_str)
    assert data["meeting_id"] == "test-meeting-101"
    assert len(data["graph_mutations"]["entity_upserts"]) >= 1
