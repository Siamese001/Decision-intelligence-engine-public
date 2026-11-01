"""Adversarial and edge-case tests for MultiHopQueryNavigatorAgent and CompetitiveQuerySwarm.

Covers mandatory edge-case categories:
1. Boundary & malformed input (empty query, invalid max_steps, missing SQLite database path).
2. Stagnation & cyclic graph termination (2-node cycle A <-> B halts cleanly via STAGNATED without infinite loop).
3. Invariant bypass attempt (readonly=False rejected; INTERNAL_ADVISORY / quarantined shadow assertions scrubbed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.query_core.navigator_agent import MultiHopQueryNavigatorAgent
from dual_pipeline.query_swarm import QueryRouteSubAgent


def test_navigator_boundary_and_malformed_inputs_rejected(tmp_path: Path) -> None:
    """Category 1: Boundary & malformed parameters fail closed."""
    with pytest.raises(ValueError, match="max_steps"):
        MultiHopQueryNavigatorAgent(max_steps=0)

    with pytest.raises(ValueError, match="max_tool_calls"):
        MultiHopQueryNavigatorAgent(max_tool_calls=0)

    nav = MultiHopQueryNavigatorAgent(max_steps=3, max_tool_calls=6)
    with pytest.raises(ValueError, match="non-empty"):
        nav.navigate(query="   ", seed_entities=["NodeA"])

    with pytest.raises(FileNotFoundError):
        nav.navigate(
            query="Valid question",
            seed_entities=["NodeA"],
            db_path=tmp_path / "nonexistent_graph.sqlite",
        )

    sub = QueryRouteSubAgent("ROUTE_3_GRAPH_TRAVERSAL")
    empty_eval = sub.evaluate_query("   ")
    assert empty_eval["confidence"] == 0.0


def test_navigator_cyclic_graph_halts_on_stagnation() -> None:
    """Category 2: Cyclic 2-node graph halts deterministically via STAGNATED when hop 2 adds zero edges."""
    cyclic_assertions = [
        {
            "assertion_id": "cycle-1",
            "subject_id": "Node_Alpha",
            "predicate": "DEPENDS_ON",
            "object_id": "Node_Beta",
            "claim_text": "Node_Alpha depends on Node_Beta.",
            "source_id": "src:cycle",
        },
        {
            "assertion_id": "cycle-2",
            "subject_id": "Node_Beta",
            "predicate": "DEPENDS_ON",
            "object_id": "Node_Alpha",
            "claim_text": "Node_Beta depends on Node_Alpha.",
            "source_id": "src:cycle",
        },
    ]
    nav = MultiHopQueryNavigatorAgent(max_steps=3, max_tool_calls=6)
    res = nav.navigate(
        query="How does Node_Alpha reach Node_Omega?",
        seed_entities=["Node_Alpha"],
        subquestions=["How does Node_Beta connect to Node_Omega?"],
        assertions=cyclic_assertions,
    )
    assert res.terminal_outcome == "STAGNATED"
    assert res.steps_executed == 2
    assert len(res.expanded_assertions) == 2


def test_navigator_blocks_write_mode_and_quarantines_internal_advisory() -> None:
    """Category 3: Rejects readonly=False and scrubs INTERNAL_ADVISORY shadow assertions."""
    nav = MultiHopQueryNavigatorAgent(max_steps=2, max_tool_calls=4)

    with pytest.raises(PermissionError, match="READ_ONLY_SNAPSHOT_REQUIRED"):
        nav.navigate(
            query="Attempt write access",
            seed_entities=["Node_A"],
            readonly=False,
        )

    mixed_assertions = [
        {
            "assertion_id": "safe-1",
            "subject_id": "Project_Atlas",
            "predicate": "ADDRESSES",
            "object_id": "Goal_Resilience",
            "claim_text": "Project_Atlas addresses Goal_Resilience.",
            "namespace": "CANONICAL",
            "is_quarantined": 0,
            "stealth_score": 0.0,
        },
        {
            "assertion_id": "shadow-leak-1",
            "subject_id": "Goal_Resilience",
            "predicate": "DEPENDS_ON",
            "object_id": "Secret_Career_Node",
            "claim_text": "Private advisory note.",
            "namespace": "INTERNAL_ADVISORY",
            "is_quarantined": 1,
            "stealth_score": 1.0,
        },
    ]
    res = nav.navigate(
        query="What does Project_Atlas address?",
        seed_entities=["Project_Atlas"],
        subquestions=["What goal does Project_Atlas address?"],
        assertions=mixed_assertions,
    )
    assert res.quarantined_filtered_count == 1
    assert "Secret_Career_Node" not in res.expanded_entities
    assert all(a["assertion_id"] != "shadow-leak-1" for a in res.expanded_assertions)
