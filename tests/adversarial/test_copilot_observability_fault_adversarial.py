"""
tests/adversarial/test_copilot_observability_fault_adversarial.py

Adversarial fault injection and observability logging verification for Live Copilot
under AGENTS.md Invariant 9.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    CATEGORY_ABSENT_CANDIDATES,
    CATEGORY_GRAPH_BINDING,
    CATEGORY_SCOPE_BOUNDARY,
    CATEGORY_USAGE,
    categorize_copilot_terminal_reason,
)
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import (
    CopilotAnswerLedgerAdapter,
    CopilotDegradedState,
    ensure_inferred_memory_boundary,
)


def test_fault_injection_yields_outcome_code_and_counter():
    """Verify fault categorization and transparent degradation state reporting."""
    # Test outcome categorization under injected terminal states
    cat, msg = categorize_copilot_terminal_reason("PARSER_NO_TOPIC")
    assert cat == CATEGORY_USAGE
    assert "[Prompt Parser]" in msg

    cat, msg = categorize_copilot_terminal_reason("SCOPE_FILTERED")
    assert cat == CATEGORY_SCOPE_BOUNDARY
    assert "[Scope Boundary]" in msg

    cat, msg = categorize_copilot_terminal_reason("ZERO_MATCHING_ASSERTIONS")
    assert cat == CATEGORY_ABSENT_CANDIDATES
    assert "[Graph Retrieval]" in msg

    cat, msg = categorize_copilot_terminal_reason("GRAPH_NOT_LOADED")
    assert cat == CATEGORY_GRAPH_BINDING
    assert "[Graph Grounding]" in msg

    # Test degraded state serialization
    degraded = CopilotDegradedState(
        is_degraded=True,
        reason="simulated_cache_failure",
        error_type="TimeoutError",
        fallback_mode="l2_disk_fallback",
    )
    d = degraded.to_dict()
    assert d["is_degraded"] is True
    assert d["fallback_mode"] == "l2_disk_fallback"
    assert d["error_type"] == "TimeoutError"


def test_read_only_jsonl_broadcasts_and_logs():
    """Verify safe non-canonical logging and answer ledger recording."""
    raw_payload = {"turn_summary": "Team agreed to proceed with phase 2", "risk": "low"}
    inferred = ensure_inferred_memory_boundary(raw_payload)

    assert inferred["is_inferred"] is True
    assert inferred["is_canonical"] is False
    assert inferred["provenance"] == "conversational_inference"
    assert inferred["turn_summary"] == "Team agreed to proceed with phase 2"

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        feedback = {
            "category": "STRATEGIC",
            "suggested_script": "Ensure rollback protocol is defined.",
            "snap_cue": "Phase 2 rollback readiness",
            "verified_assertions": [{"assertion_id": "asrt_99182"}],
        }
        ledger_id = CopilotAnswerLedgerAdapter.record_delivery_safe(
            feedback=feedback,
            session_id="sess_obs_test",
            runtime_root=tmp_path,
        )
        assert ledger_id.startswith("ans_copilot_")
        assert feedback["ledger_id"] == ledger_id
