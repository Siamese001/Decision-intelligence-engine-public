"""Regression tests for Gate 3.4 quote fidelity verifying < 1% mismatch under position-pointer architecture."""
from __future__ import annotations

from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
from dual_pipeline.transcript_stages_core.quote_pointer_resolver import resolve_evidence_pointers


def test_gate_3_4_quote_fidelity_with_position_pointers():
    """Verify Gate 3.4 achieves 100% quote grounding (0% mismatch) when using position pointers."""
    d01_forensic_transcript = (
        "## Forensic Attributed Transcript\n\n"
        "[00:01:05] Alex Brennan: The architecture uses Gemini 3.8 Flash for D02 and D03 extraction.\n"
        "[00:02:15] Amit Ayer: We require exact character pointers to guarantee Gate 3.4 fidelity.\n"
        "[00:03:30] Shreyak Gupta: The BGE-M3 retrieval evaluations must pass all eleven non-compensating gates.\n"
    )

    # Calculate exact offsets in d01
    quote1 = "The architecture uses Gemini 3.8 Flash for D02 and D03 extraction."
    quote2 = "We require exact character pointers to guarantee Gate 3.4 fidelity."
    quote3 = "The BGE-M3 retrieval evaluations must pass all eleven non-compensating gates."

    p1_start = d01_forensic_transcript.index(quote1)
    p1_end = p1_start + len(quote1)

    p2_start = d01_forensic_transcript.index(quote2)
    p2_end = p2_start + len(quote2)

    p3_start = d01_forensic_transcript.index(quote3)
    p3_end = p3_start + len(quote3)

    raw_projection = {
        "schema_version": "5.7",
        "evidence_index": {
            "ev-01": {
                "kind": "technical",
                "speaker": "Alex Brennan",
                "quote_pointer": [p1_start, p1_end],
            },
            "ev-02": {
                "kind": "decision",
                "speaker": "Amit Ayer",
                "quote_pointer": [p2_start, p2_end],
            },
            "ev-03": {
                "kind": "strategic",
                "speaker": "Shreyak Gupta",
                "quote_pointer": [p3_start, p3_end],
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:nexus", "name": "Nexus System", "type": "system", "mutation": "UPSERT", "confidence": 0.95},
                {"id": "person:alex_brennan", "name": "Alex Brennan", "type": "person", "mutation": "UPSERT", "confidence": 0.95},
            ],
            "relationship_signals": [
                {"signal_id": "sig-01", "from_id": "person:alex_brennan", "to_id": "system:nexus", "predicate": "MAINTAINS", "relationship": "MAINTAINS", "confidence": 0.90},
            ],
            "decision_updates": [],
            "risk_updates": [],
            "action_updates": [],
        },
    }

    # Resolve pointers against d01 source
    resolved_projection = resolve_evidence_pointers(raw_projection, source_text=d01_forensic_transcript)

    # Run QA Gate 3
    qa_result = run_qa_gate_3(
        f3_text=resolved_projection,
        f1_text=d01_forensic_transcript,
    )

    # Gate 3.4 evidence grounding must pass with zero failures
    grounding_failures = [f for f in qa_result["failures"] if "exact_text not grounded" in f]
    assert len(grounding_failures) == 0, f"Gate 3.4 failed with ungrounded quotes: {grounding_failures}"
    assert qa_result["gates"]["Gate_3_4_Evidence_Index_Fidelity_and_Grounding"]["passed"] is True


def test_gate_3_4_catches_unresolved_hallucinated_quotes():
    """Verify Gate 3.4 catches free-form hallucinated quotes when pointers are absent."""
    d01_forensic_transcript = (
        "## Forensic Attributed Transcript\n\n"
        "[00:01:05] Alex Brennan: We are deploying the model on Monday.\n"
    )

    hallucinated_projection = {
        "schema_version": "5.7",
        "evidence_index": {
            "ev-01": {
                "kind": "technical",
                "speaker": "Alex Brennan",
                # Hallucinated quote not present in d01
                "exact_text": "We will totally launch the product next Friday morning after lunch.",
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:nexus", "name": "Nexus", "type": "system", "mutation": "UPSERT", "confidence": 0.95},
            ],
            "relationship_signals": [],
            "decision_updates": [],
            "risk_updates": [],
            "action_updates": [],
        },
    }

    qa_result = run_qa_gate_3(
        f3_text=hallucinated_projection,
        f1_text=d01_forensic_transcript,
    )

    # Gate 3.4 should fail closed for the hallucinated quote
    grounding_failures = [f for f in qa_result["failures"] if "exact_text not grounded" in f]
    assert len(grounding_failures) == 1
    assert qa_result["gates"]["Gate_3_4_Evidence_Index_Fidelity_and_Grounding"]["passed"] is False
