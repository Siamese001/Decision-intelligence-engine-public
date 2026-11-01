"""
tests/test_query_enhanced_output3_graph.py

Wave 4 Verification:
Tests Output 03 Canonical Signal Projection prompt assembly and graph grounding:
1. Verifies that known graph entities from offline retrieval are injected into Stage 03 prompt.
2. Verifies that Rule C-16 (CONFIRM known vs CREATE new) instructions are explicitly declared.
3. Verifies that Rule C-03 (entity prefix enforcement) instructions are present.
4. Verifies clean omission when known_graph_entities is absent.
"""
from __future__ import annotations

from typing import Any, Dict

from dual_pipeline.transcript_stages_core.graph_executor import build_stage03_user_content


def test_graph_executor_injects_known_entities_and_rule_c16() -> None:
    """Verifies that known_graph_entities block is injected with Rule C-16 instructions."""
    metadata: Dict[str, Any] = {
        "meeting_name": "Turbine Migration Review",
        "date": "09/20/2026",
        "archetype": "INTERNAL_TECHNICAL_SYNC",
        "known_graph_entities": [
            {
                "node_id": "project:nexus",
                "canonical_name": "Project Nexus",
                "display_name": "Project Nexus",
                "node_type": "project",
            },
            {
                "node_id": "system:turbines_hub",
                "canonical_name": "Turbines Hub",
                "display_name": "Turbines Hub",
                "node_type": "system",
            },
        ],
    }

    transcript = "Amit Ayer: We confirmed Turbines Hub will interface with Project Nexus."
    user_content = build_stage03_user_content(transcript, metadata)

    assert "<known_graph_entities>" in user_content
    assert "project:nexus" in user_content
    assert "system:turbines_hub" in user_content
    assert "Rule C-16" in user_content
    assert "mutation: 'CONFIRM'" in user_content
    assert "mutation: 'CREATE'" in user_content
    assert "Rule C-03" in user_content


def test_graph_executor_omits_known_entities_when_absent() -> None:
    """Verifies clean omission of <known_graph_entities> when absent in metadata."""
    metadata: Dict[str, Any] = {
        "meeting_name": "Ad-Hoc Session",
        "date": "09/20/2026",
        "archetype": "INTERNAL_TECHNICAL_SYNC",
    }

    transcript = "Discussion on general topics."
    user_content = build_stage03_user_content(transcript, metadata)

    assert "<known_graph_entities>" not in user_content
