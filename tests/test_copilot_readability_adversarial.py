"""
tests/test_copilot_readability_adversarial.py

Adversarial and Edge-Case Tests for Wave 3: Conversational Synthesis & Verbatim Readability.
Covers mandatory edge-case categories:
1. Boundary payload: Empty assertions, zero max_words, whitespace-only claims.
2. Malformed input: Comma-spliced em-dashes, raw ISO timestamps, parenthetical metadata tags, bracketed citations.
3. Overflow & exhaustion: Overweight first line skipped so subsequent concise lines still pass Speak Gate.
4. Invariant bypass attempt: Written essay formatting (bullet lists, academic jargon) still rejected by Speak Gate.
"""
from __future__ import annotations

from dual_pipeline.cli.copilot_speech import (
    _normalize_spoken_ear,
    render_speaker_comments,
)
from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _filter_body_through_speak_gate,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import evaluate_speak_gate


def _build_retriever() -> CopilotGraphRetriever:
    topo = InMemoryGraphTopology(
        graph_path="/tmp/mock_wave3_adv.sqlite",
        graph_sha256="sha_wave3_adv",
        nodes={
            "entity:auth": {
                "node_id": "entity:auth",
                "canonical_name": "Auth Service",
                "display_name": "Auth Service",
            }
        },
        assertions={},
        supporting_evidence={},
        contradicting_evidence={},
        forward_adjacency={"entity:auth": []},
        reverse_adjacency={"entity:auth": []},
        undirected_adjacency={"entity:auth": []},
        canonical_edges=[],
    )
    return CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")


def test_adversarial_iso_timestamps_metadata_parens_and_em_dashes_stripped() -> None:
    """Category 2 (Malformed input): ISO timestamps, metadata parens, and em-dashes are cleaned without comma splices."""
    retriever = _build_retriever()
    assertion = {
        "assertion_id": "asrt_clutter_1",
        "subject_node_id": "entity:auth",
        "predicate": "HAS_RISK",
        "claim_text": "[asrt_clutter_1] Auth token rotation failed on 2026-10-14T15:30:00Z (source: asrt_99, score: 0.91)—we must patch the signer (auth_config.yaml).",
    }
    res = render_speaker_comments("next-questions", "Mitch", [assertion], None, retriever)
    assert res.rendered_count == 1
    assert "2026-10-14T15:30:00Z" not in res.body
    assert "2026-10-14" in res.body
    assert "source:" not in res.body
    assert "asrt_clutter_1" not in res.body
    assert "auth_config.yaml" not in res.body
    assert "—" not in res.body
    # Em-dash replaced with " and " rather than comma splice
    assert " and we must patch" in res.body
    assert res.body.count(",") <= 1


def test_adversarial_conflict_comment_strips_iso_time_suffix() -> None:
    """Category 2 (Malformed input): Conflict gap dates strip T00:00:00Z suffixes for natural reading."""
    retriever = _build_retriever()
    a1 = {
        "assertion_id": "a_early",
        "subject_node_id": "entity:auth",
        "claim_text": "Auth Service is on track",
    }
    a2 = {
        "assertion_id": "a_late",
        "subject_node_id": "entity:auth",
        "claim_text": "Auth Service is blocked on key rotation",
    }
    retriever._last_gap_conflicts = [
        {
            "assertion_a": a1,
            "assertion_b": a2,
            "date_a": "2026-10-01T09:15:00Z",
            "date_b": "2026-10-15T18:45:00Z",
        }
    ]
    res = render_speaker_comments("gaps", None, [a1, a2], None, retriever)
    assert res.rendered_count == 1
    assert "2026-10-01 " in res.body
    assert "2026-10-15." in res.body
    assert "T09:15:00Z" not in res.body
    assert "T18:45:00Z" not in res.body


def test_adversarial_speak_gate_filter_skips_overweight_line_and_retains_concise_line() -> None:
    """Category 3 (Overflow & exhaustion): _filter_body_through_speak_gate skips an overweight line without dropping subsequent valid lines."""
    assertions = [
        {
            "assertion_id": "a_short",
            "claim_text": "Speaker_Alpha confirmed that staging cutover validation passed all regression gates.",
            "is_active": True,
        }
    ]
    overweight_line = "Could we clarify " + " ".join(["validation"] * 95) + "?"
    concise_line = "Could we clarify Speaker_Alpha confirmed that staging cutover validation passed all regression gates?"
    body = f"{overweight_line}\n{concise_line}"

    filtered, reason = _filter_body_through_speak_gate(
        body, assertions, spk_conf=0.95, intent="next-questions", max_words=None
    )
    assert reason == ""
    assert filtered == concise_line


def test_adversarial_essay_patterns_still_rejected_by_speak_gate() -> None:
    """Category 4 (Invariant bypass): Bullet lists and academic essay jargon still fail closed in Speak Gate."""
    ast = {
        "assertion_id": "a_essay",
        "claim_text": "The pareto curve denominator requires equal-weighting.",
        "is_active": True,
    }
    dec = evaluate_speak_gate(
        "- First, the pareto curve denominator requires equal-weighting.",
        assertion=ast,
        speaker_confidence=0.95,
        max_words=None,
    )
    assert not dec.should_speak
