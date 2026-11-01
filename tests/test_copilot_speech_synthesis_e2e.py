"""
tests/test_copilot_speech_synthesis_e2e.py

End-to-end tests for Wave 4: Contextual Question Synthesis & Speech Rendering.
Verifies that:
1. Synthesized questions bridge live speaker dialogue and graph context ("To Voice A's point, ...").
2. Targeted responses combine target vocative and speaker reference ("Mitch, regarding Voice A's point, ...").
3. Next-actions queries ground action synthesis in live speaker remarks.
4. Raw extraction metadata strings ("is an identified technical_anchor in") are strictly rejected from speech output.
"""
from __future__ import annotations

from typing import Any, Dict
import pytest

from dual_pipeline.cli.copilot_speech import (
    render_speaker_comments,
    speaker_comments,
    spoken_claim,
)
from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


def _create_mock_retriever() -> CopilotGraphRetriever:
    topo = InMemoryGraphTopology(
        graph_path="/tmp/mock_speech.sqlite",
        graph_sha256="mock_hash_speech",
        nodes={
            "entity-vdb": {
                "node_id": "entity-vdb",
                "canonical_name": "Vector Database",
                "display_name": "Vector Database",
            }
        },
        assertions={},
        supporting_evidence={},
        contradicting_evidence={},
        forward_adjacency={"entity-vdb": []},
        reverse_adjacency={"entity-vdb": []},
        undirected_adjacency={"entity-vdb": []},
        canonical_edges=[],
    )
    return CopilotGraphRetriever(topology=topo, engagement_id="project:project_nexus")


def test_copilot_speech_references_live_speaker_turn():
    """Verify next-questions connects live speaker remarks with graph risk assertion."""
    retriever = _create_mock_retriever()
    selected_utterance = {
        "speaker": "Voice A",
        "text": "And you can always set up a Vector database",
        "timestamp": "00:07:40",
    }
    assertions = [
        {
            "assertion_id": "ast-risk-1",
            "subject_node_id": "entity-vdb",
            "predicate": "HAS_RISK",
            "claim_text": "Vector database memory scaling introduces latency spikes.",
            "object_literal": "Vector database memory scaling introduces latency spikes.",
        }
    ]

    res = render_speaker_comments(
        intent="next-questions",
        target=None,
        assertions=assertions,
        max_words=None,
        retriever=retriever,
        selected_utterance=selected_utterance,
    )
    assert res.rendered_count == 1
    assert "To Voice A's point" in res.body
    assert "vector database memory scaling introduces latency spikes" in res.body
    assert "is an identified technical_anchor in" not in res.body


def test_copilot_speech_targeted_respond_with_speaker_context():
    """Verify respond intent with target combines attendee name and speaker attribution."""
    retriever = _create_mock_retriever()
    selected_utterance = {
        "speaker": "Voice A",
        "text": "We can build the index today.",
        "timestamp": "00:08:15",
    }
    assertions = [
        {
            "assertion_id": "ast-risk-1",
            "subject_node_id": "entity-vdb",
            "predicate": "HAS_RISK",
            "claim_text": "Index rebuild invalidates active read caches during business hours.",
        }
    ]

    res = render_speaker_comments(
        intent="respond",
        target="Mitch",
        assertions=assertions,
        max_words=None,
        retriever=retriever,
        selected_utterance=selected_utterance,
    )
    assert res.rendered_count == 1
    assert res.body.startswith("Mitch, regarding Voice A's point, could we clarify")
    assert "index rebuild invalidates active read caches" in res.body


def test_copilot_speech_next_actions_with_speaker_context():
    """Verify next-actions incorporates speaker reference and action phrasing."""
    retriever = _create_mock_retriever()
    selected_utterance = {
        "speaker": "Voice A",
        "text": "Who is driving the pipeline?",
        "timestamp": "00:09:00",
    }
    assertions = [
        {
            "assertion_id": "ast-act-1",
            "subject_node_id": "entity-vdb",
            "predicate": "OWNS_OR_LEADS",
            "claim_text": "Data Engineering owns the vector database indexing pipeline.",
        }
    ]

    res = render_speaker_comments(
        intent="next-actions",
        target=None,
        assertions=assertions,
        max_words=None,
        retriever=retriever,
        selected_utterance=selected_utterance,
    )
    assert res.rendered_count == 1
    assert res.body.startswith("To Voice A's point, what is the next step for")
    assert "data Engineering owns the vector database indexing pipeline" in res.body


def test_copilot_speech_metadata_strings_filtered_out():
    """Verify spoken_claim rejects extraction metadata and render_speaker_comments marks malformed."""
    retriever = _create_mock_retriever()
    metadata_assertion = {
        "assertion_id": "ast-raw-meta",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_CLAIM_TEXT",
        "object_literal": "Persistent Database is an identified technical_anchor in 20260930 Pipeline Review.",
        "claim_text": "Persistent Database is an identified technical_anchor in 20260930 Pipeline Review.",
    }

    # spoken_claim must return empty string for metadata patterns
    assert spoken_claim(metadata_assertion, retriever) == ""

    # render_speaker_comments must reject and not emit metadata into body
    res = render_speaker_comments(
        intent="next-questions",
        target=None,
        assertions=[metadata_assertion],
        max_words=None,
        retriever=retriever,
    )
    assert res.body == ""
    assert res.malformed_count == 1
    assert "is an identified technical_anchor in" not in res.body


def test_copilot_speech_gaps_intent_natural_spoken_flow():
    """Verify gaps intent generates natural spoken verification without 'note that'."""
    retriever = _create_mock_retriever()
    selected_utterance = {
        "speaker": "Voice A",
        "text": "Are we sure about the cache?",
        "timestamp": "00:10:00",
    }
    assertion = {
        "assertion_id": "ast-gap-1",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "Vector cache invalidation policy requires two-node consensus.",
    }

    res = render_speaker_comments(
        intent="gaps",
        target="Mitch",
        assertions=[assertion],
        max_words=None,
        retriever=retriever,
        selected_utterance=selected_utterance,
    )
    assert res.rendered_count == 1
    assert "Mitch, we should confirm whether" in res.body
    assert "note that" not in res.body.lower()
    assert "what is the gap here" not in res.body.lower()
    assert len(res.body.split()) <= 35


def test_copilot_speech_review_and_default_intents():
    """Verify review and default intents use natural spoken phrasing without 'our records show'."""
    retriever = _create_mock_retriever()
    assertion = {
        "assertion_id": "ast-rev-1",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "Production vector cluster memory is capped at 64 gigabytes.",
    }

    # Review intent
    res_review = render_speaker_comments("review", "Sharon", [assertion], None, retriever)
    assert res_review.rendered_count == 1
    assert "our agreed baseline is that" in res_review.body.lower()
    assert len(res_review.body.split()) <= 35

    # Default intent
    res_default = render_speaker_comments("other_unknown", "Sharon", [assertion], None, retriever)
    assert res_default.rendered_count == 1
    assert "we're tracking that" in res_default.body.lower()
    assert "our records show" not in res_default.body.lower()
    assert len(res_default.body.split()) <= 35


def test_copilot_speech_e2e_conversational_calibration():
    """Verify end-to-end ear-first spoken delivery satisfies word budget and natural phrasing."""
    retriever = _create_mock_retriever()
    utterance = {
        "speaker": "Voice A",
        "text": "What about the sharding roadmap?",
        "timestamp": "00:12:30",
    }
    assertion = {
        "assertion_id": "ast-sharding-roadmap",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "We are targeting accuracy >= 95% across all shards (config.yaml)—we need consensus.",
    }

    res = render_speaker_comments("respond", "Andy", [assertion], 35, retriever, selected_utterance=utterance)
    assert res.rendered_count == 1
    assert "Andy, regarding Voice A's point, could we clarify" in res.body
    assert "at least 95%" in res.body
    assert "config.yaml" not in res.body
    assert "we're targeting" in res.body.lower()
    assert len(res.body.split()) <= 35

