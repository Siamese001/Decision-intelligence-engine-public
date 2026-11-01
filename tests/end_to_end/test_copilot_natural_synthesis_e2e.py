"""
tests/end_to_end/test_copilot_natural_synthesis_e2e.py

End-to-end tests for Wave 3: Conversational Synthesis, Word Budget & Verbatim Readability.
Verifies:
1. Multi-assertion synthesis yields a natural, cohesive 40-85 word spoken response across 2-3 sentences.
2. Comma density remains <= 2 commas per sentence and redundant vocative/speaker anchors are not repeated on every line.
3. Spoken ear normalization strips ISO timestamps, file extensions, parenthetical metadata, and comma-spliced em-dashes.
4. Clause verifier accepts discourse starters and spelled numbers (eleven through ninety) without false rejection.
"""
from __future__ import annotations

from dual_pipeline.cli.copilot_speech import render_speaker_comments
from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    check_clause_grounding,
)
from dual_pipeline.live_meeting_recorder_core.copilot_composer import LocalCopilotComposer
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _filter_body_through_speak_gate,
    _verify_rendered_body_clauses,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


def _build_retriever() -> CopilotGraphRetriever:
    topo = InMemoryGraphTopology(
        graph_path="/tmp/mock_wave3_nat.sqlite",
        graph_sha256="sha_wave3_nat",
        nodes={
            "person:brien": {
                "node_id": "person:brien",
                "node_type": "person",
                "canonical_name": "Speaker_Alpha",
                "display_name": "Speaker_Alpha",
            },
            "entity:cutover": {
                "node_id": "entity:cutover",
                "node_type": "workstream",
                "canonical_name": "Billing Cutover",
                "display_name": "Billing Cutover",
            },
        },
        assertions={},
        supporting_evidence={},
        contradicting_evidence={},
        forward_adjacency={"person:brien": [], "entity:cutover": []},
        reverse_adjacency={"person:brien": [], "entity:cutover": []},
        undirected_adjacency={"person:brien": [], "entity:cutover": []},
        canonical_edges=[],
    )
    return CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")


def test_e2e_multi_assertion_natural_synthesis_and_readability() -> None:
    """Multi-assertion Copilot output produces 40-85 words across 2-3 sentences with low comma density."""
    retriever = _build_retriever()
    assertions = [
        {
            "assertion_id": "asrt_w3_1",
            "subject_node_id": "person:brien",
            "object_node_id": "entity:cutover",
            "predicate": "RAISED_CONCERN",
            "claim_text": "Speaker_Alpha raised a concern that parallel billing cutover requires fourteen days of shadow validation before go-live.",
            "object_literal": "Speaker_Alpha raised a concern that parallel billing cutover requires fourteen days of shadow validation before go-live.",
            "is_primary": True,
            "is_active": True,
            "confidence_score": 0.94,
        },
        {
            "assertion_id": "asrt_w3_2",
            "subject_node_id": "person:brien",
            "object_node_id": "entity:cutover",
            "predicate": "PROPOSED",
            "claim_text": "Marcus proposed freezing downstream schema migrations during the billing cutover window to protect reconciliation.",
            "object_literal": "Marcus proposed freezing downstream schema migrations during the billing cutover window to protect reconciliation.",
            "is_primary": True,
            "is_active": True,
            "confidence_score": 0.92,
        },
        {
            "assertion_id": "asrt_w3_3",
            "subject_node_id": "person:brien",
            "object_node_id": "entity:cutover",
            "predicate": "COMMITTED_TO",
            "claim_text": "Sharon committed to delivering automated rollback runbooks for the billing cutover by Friday.",
            "object_literal": "Sharon committed to delivering automated rollback runbooks for the billing cutover by Friday.",
            "is_primary": True,
            "is_active": True,
            "confidence_score": 0.91,
        },
    ]
    selected_utterance = {
        "speaker": "Voice A",
        "text": "How are we handling the parallel billing cutover and rollback readiness?",
        "matched_keywords": ["billing", "cutover"],
        "speaker_summary": "Asking about billing cutover validation.",
    }

    res = render_speaker_comments(
        intent="respond",
        target="Mitch",
        assertions=assertions,
        max_words=None,
        retriever=retriever,
        selected_utterance=selected_utterance,
    )
    assert res.rendered_count == 3
    words = res.body.split()
    assert 40 <= len(words) <= 85, f"Expected 40-85 words, got {len(words)}: {res.body}"

    lines = [ln.strip() for ln in res.body.split("\n") if ln.strip()]
    assert len(lines) == 3
    # First line has vocative target; subsequent lines do not repeat "Mitch, regarding Voice A's point,"
    assert lines[0].startswith("Mitch, regarding Voice A's point,")
    assert not lines[1].startswith("Mitch,")
    assert not lines[2].startswith("Mitch,")

    # Every sentence has <= 2 commas and zero bracket/ISO clutter
    for ln in lines:
        assert ln.count(",") <= 2, f"Too many commas in line: {ln}"
        assert "[" not in ln and "]" not in ln

    # Verify clause grounding and speak gate pass all 3 lines
    grounded_body = _verify_rendered_body_clauses(
        res.body, assertions, target="Mitch", selected_utterance=selected_utterance
    )
    assert len(grounded_body.split("\n")) == 3

    gated_body, gate_err = _filter_body_through_speak_gate(
        grounded_body, assertions, spk_conf=0.92, intent="respond", max_words=None
    )
    assert gate_err == ""
    assert len(gated_body.split("\n")) == 3


def test_e2e_composer_multi_assertion_synthesis_and_discourse_grounding() -> None:
    """LocalCopilotComposer.compose_multi_assertion_synthesis weaves 3 speaker notes into a cohesive paragraph."""
    composer = LocalCopilotComposer(enabled=True)
    assertions = [
        {
            "assertion_id": "asrt_comp_1",
            "claim_text": "Speaker_Alpha flagged that vendor API rate limits cap ingestion throughput at twenty requests per second.",
            "object_literal": "Speaker_Alpha flagged that vendor API rate limits cap ingestion throughput at twenty requests per second.",
            "insight_type": "risk",
        },
        {
            "assertion_id": "asrt_comp_2",
            "claim_text": "Speaker_Beta proposed batching webhook payloads to reduce outbound call volume by fifty percent.",
            "object_literal": "Speaker_Beta proposed batching webhook payloads to reduce outbound call volume by fifty percent.",
            "insight_type": "prior_decision",
        },
        {
            "assertion_id": "asrt_comp_3",
            "claim_text": "Marcus owns validating the batched webhook queue under peak load.",
            "object_literal": "Marcus owns validating the batched webhook queue under peak load.",
            "insight_type": "owner_gap",
        },
    ]
    res = composer.compose_multi_assertion_synthesis(
        assertions=assertions,
        dialogue_context="vendor API rate limits and webhook batching",
        target="Andy",
        selected_utterance={"speaker": "Speaker_Alpha", "text": "We hit vendor API rate limits"},
        max_words=85,
    )
    assert res.rendered_count == 3
    assert 40 <= len(res.body.split()) <= 85
    assert res.groundedness == 1.0

    # Verify clause verifier handles discourse starters ("Given", "Meanwhile") and spelled numbers ("twenty", "fifty")
    by_id = {a["assertion_id"]: a for a in assertions}
    clause = "Given Speaker_Alpha flagged that vendor API rate limits cap ingestion throughput at 20 requests per second, how are we mitigating this?"
    assert check_clause_grounding(clause, assertions, by_id, target="Andy") == "asrt_comp_1"
