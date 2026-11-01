"""End-to-end integration test for D03 position-pointer quote lifecycle through post extraction and QA gates."""
from __future__ import annotations

from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
from dual_pipeline.transcript_stages_core.graph_post_extractor import (
    sanitize_and_validate_projection,
)


def test_d03_quote_pointer_lifecycle_e2e():
    """E2E lifecycle: D01 text -> D03 pointer generation -> sanitize_and_validate -> Gate 3 verification."""
    d01_text = (
        "## Forensic Transcript\n\n"
        "[00:01:00] Alex Brennan: LENS is the core asset intelligence system under active evaluation.\n"
        "[00:02:00] Amit Ayer: The contract requires schema version 5.7 and strict prefix domain invariants.\n"
    )

    quote1 = "LENS is the core asset intelligence system under active evaluation."
    quote2 = "The contract requires schema version 5.7 and strict prefix domain invariants."

    p1_start = d01_text.index(quote1)
    p1_end = p1_start + len(quote1)

    p2_start = d01_text.index(quote2)
    p2_end = p2_start + len(quote2)

    raw_projection = {
        "schema_version": "5.7",
        "evidence_index": {
            "ev-lens": {
                "kind": "technical",
                "speaker": "Alex Brennan",
                "quote_pointer": [p1_start, p1_end],
            },
            "ev-contract": {
                "kind": "strategic",
                "speaker": "Amit Ayer",
                "quote_pointer": [p2_start, p2_end],
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:lens", "name": "LENS", "type": "system", "mutation": "UPSERT", "confidence": 0.95},
                {"id": "person:alex_brennan", "name": "Alex Brennan", "type": "person", "mutation": "UPSERT", "confidence": 0.95},
            ],
            "relationship_signals": [
                {"signal_id": "sig-01", "from_id": "person:alex_brennan", "to_id": "system:lens", "predicate": "EVALUATES", "relationship": "EVALUATES", "confidence": 0.90},
            ],
            "decision_updates": [],
            "risk_updates": [],
            "action_updates": [],
        },
    }

    # Step 1: Pass through sanitize_and_validate_projection
    sanitized_projection = sanitize_and_validate_projection(
        raw_projection=raw_projection,
        metadata={"date": "09/25/2026"},
        source_text=d01_text,
    )

    # Step 2: Verify that quote_pointer was resolved to verbatim text
    ev_lens = sanitized_projection["evidence_index"]["ev-lens"]
    assert ev_lens["exact_text"] == quote1
    assert ev_lens["quote_pointer"] == [p1_start, p1_end]

    # Step 3: Run full QA Gate 3
    qa_result = run_qa_gate_3(
        f3_text=sanitized_projection,
        f1_text=d01_text,
    )

    assert qa_result["gates"]["Gate_3_4_Evidence_Index_Fidelity_and_Grounding"]["passed"] is True
    assert qa_result["passed"] is True
