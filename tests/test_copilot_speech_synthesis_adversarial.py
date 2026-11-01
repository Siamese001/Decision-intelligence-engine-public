"""
tests/test_copilot_speech_synthesis_adversarial.py

Adversarial tests for Wave 4: Contextual Question Synthesis & Speech Rendering.
Covers 4 mandatory edge-case categories:
1. Boundary payload: empty utterances, None speakers, 0 max_words.
2. Malformed input: code injection in speaker names, markdown triple-backticks, non-string text.
3. Overflow & budget exhaustion: tight word limits triggering WORD_BUDGET_EXHAUSTED.
4. Exact production regression: Voice A at 07:40 synthesized with valid vector database assertion.
"""
from __future__ import annotations

from typing import Any, Dict
import pytest

from dual_pipeline.cli.copilot_speech import (
    render_speaker_comments,
    speaker_comments,
)
from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


def _create_mock_retriever() -> CopilotGraphRetriever:
    topo = InMemoryGraphTopology(
        graph_path="/tmp/mock_speech_adv.sqlite",
        graph_sha256="mock_hash_speech_adv",
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


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payloads - None utterances, empty strings, 0 max_words."""
    retriever = _create_mock_retriever()
    assertion = {
        "assertion_id": "ast-1",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "Vector database memory scaling introduces latency spikes.",
    }

    # None selected_utterance falls back cleanly without crash
    res_none = render_speaker_comments("next-questions", None, [assertion], None, retriever, selected_utterance=None)
    assert res_none.rendered_count == 1
    assert res_none.body == "Could we clarify vector database memory scaling introduces latency spikes?"

    # Empty dictionary utterance falls back cleanly
    res_empty = render_speaker_comments("next-questions", None, [assertion], None, retriever, selected_utterance={})
    assert res_empty.rendered_count == 1
    assert res_empty.body == "Could we clarify vector database memory scaling introduces latency spikes?"

    # max_words = 0 enforces 0 words allowed -> exhausts budget
    res_zero = render_speaker_comments("next-questions", None, [assertion], 0, retriever, selected_utterance=None)
    assert res_zero.body == ""
    assert res_zero.failure_reason == "WORD_BUDGET_EXHAUSTED"


def test_adversarial_injection_safety():
    """Category 2: Malformed input & injection safety - backtick injection in speaker or claim."""
    retriever = _create_mock_retriever()

    # Injected speaker name with code block
    injection_utterance = {
        "speaker": "Voice A```import os; os.system('echo pwned')```",
        "text": "And you can always set up a Vector database",
    }
    assertion = {
        "assertion_id": "ast-1",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "Vector database memory scaling introduces latency spikes.",
    }

    res = render_speaker_comments(
        "next-questions", None, [assertion], None, retriever, selected_utterance=injection_utterance
    )
    assert "```" not in res.body
    assert res.rendered_count == 1

    # Injected claim text with code block must be flagged malformed and rejected
    injection_assertion = {
        "assertion_id": "ast-injected",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "Vector DB ```hack``` injection",
    }
    res_claim = render_speaker_comments(
        "next-questions", None, [injection_assertion], None, retriever, selected_utterance=None
    )
    assert res_claim.body == ""
    assert res_claim.malformed_count == 1
    assert res_claim.failure_reason == "MALFORMED_REPRESENTATION"


def test_adversarial_word_budget_exhaustion():
    """Category 3: Overflow & exhaustion - strict word limit enforcement."""
    retriever = _create_mock_retriever()
    assertions = [
        {
            "assertion_id": "ast-1",
            "subject_node_id": "entity-vdb",
            "predicate": "HAS_RISK",
            "claim_text": "Vector database memory scaling introduces latency spikes.",  # 8 words + prefix = 13 words
        },
        {
            "assertion_id": "ast-2",
            "subject_node_id": "entity-vdb",
            "predicate": "CONSTRAINS",
            "claim_text": "Disk quota limits cache replication throughput.",  # 6 words
        },
    ]
    selected_utterance = {"speaker": "Voice A", "text": "Set up vector DB"}

    # Limit to 15 words: first assertion fits (13 words), second assertion exceeds (13 + 11 > 15)
    res = render_speaker_comments("next-questions", None, assertions, 15, retriever, selected_utterance=selected_utterance)
    assert res.rendered_count == 1
    assert res.budget_exhausted_count == 1
    assert len(res.body.split()) <= 15


def test_exact_production_regression_voice_a_synthesis():
    """Category 4: Exact production regression - Voice A at 07:40 natural speakable synthesis."""
    retriever = _create_mock_retriever()
    voice_a_turn = {
        "speaker": "Voice A",
        "text": "And you can always set up a Vector database",
        "timestamp": "00:07:40",
    }
    vector_assertion = {
        "assertion_id": "ast-vdb-prod",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "Vector database sharding requires dedicated cluster coordination.",
    }

    res = render_speaker_comments(
        "next-questions", None, [vector_assertion], None, retriever, selected_utterance=voice_a_turn
    )

    # 1. Output must be natural and speakable
    assert res.body == "To Voice A's point, could we clarify vector database sharding requires dedicated cluster coordination?"

    # 2. Output must NOT contain raw database metadata string patterns
    assert "is an identified technical_anchor in" not in res.body
    assert "20260930 Pipeline Review" not in res.body
    assert "20260929 Pgd Followup" not in res.body

    # 3. Output must fit in a single sentence suitable for executive copilot (< 20 words)
    assert len(res.body.split()) <= 20


def test_adversarial_spoken_ear_normalization():
    """Category 5: Spoken ear normalization - symbols, file extensions, em-dashes, contractions."""
    retriever = _create_mock_retriever()
    assertion = {
        "assertion_id": "ast-symbols-1",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "We are seeing accuracy >= 90% in our tests (contract_milestones.json)—we must verify.",
    }

    res = render_speaker_comments("next-questions", None, [assertion], None, retriever)
    assert res.rendered_count == 1
    # Check that >= became at least
    assert "at least" in res.body
    assert ">=" not in res.body
    # Check that .json in parens was stripped
    assert ".json" not in res.body
    assert "contract_milestones" not in res.body
    # Check that em-dash was replaced
    assert "—" not in res.body
    # Check that 'we are' contracted to 'we're'
    assert "we're" in res.body.lower()
    # Check word count is speakable
    assert len(res.body.split()) <= 35


def test_adversarial_zero_robotic_preambles():
    """Category 6: Zero robotic preambles across all intent branches."""
    retriever = _create_mock_retriever()
    assertion = {
        "assertion_id": "ast-risk-clean",
        "subject_node_id": "entity-vdb",
        "predicate": "HAS_RISK",
        "claim_text": "Vector database memory scaling introduces latency spikes.",
    }

    for intent in ("gaps", "respond", "review", "next-actions", "default_other"):
        res = render_speaker_comments(intent, "Mitch", [assertion], None, retriever)
        if res.body:
            low = res.body.lower()
            assert "note that" not in low, f"Robotic preamble 'note that' found in intent {intent}: {res.body}"
            assert "our records show" not in low, f"Robotic preamble 'our records show' found in intent {intent}: {res.body}"
            assert "what is the gap here" not in low, f"Robotic suffix found in intent {intent}: {res.body}"
            assert len(res.body.split()) <= 35, f"Budget exceeded in intent {intent}: {len(res.body.split())} words"


def test_prompt_and_skill_spoken_governance_invariants():
    """Category 7: Governance file audit - 3-part hierarchy, ear-first rules, and bad/good pairs."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent

    # 1. LIVE_EXECUTIVE_COPILOT_PROMPT.md audit
    prompt_path = repo_root / "governance" / "decision_intelligence" / "LIVE_EXECUTIVE_COPILOT_PROMPT.md"
    assert prompt_path.exists(), "LIVE_EXECUTIVE_COPILOT_PROMPT.md must exist"
    prompt_text = prompt_path.read_text()

    # Google 3-part hierarchy
    assert "# Persona" in prompt_text
    assert "# Conversational Rules" in prompt_text
    assert "# Guardrails" in prompt_text

    # Anthropic ear-first voice invariants
    assert "35 words total" in prompt_text
    assert "18 words per sentence" in prompt_text
    assert "Zero Preambles" in prompt_text
    assert "Note that" in prompt_text

    # Both Bad-vs-Good one-shot calibration examples present verbatim
    assert "Serial Number Accuracy" in prompt_text
    assert "Since manufacturer and model are already working well" in prompt_text
    assert "Let's lock in the manufacturer and model first" in prompt_text

    assert "1,800-Image Scale Intuition" in prompt_text
    assert "Ed, your intuition on the 1,800 images is dead on" in prompt_text
    assert "Ed, your intuition on the eighteen hundred images is spot on" in prompt_text

    # 2. SKILL.md audit
    skill_path = repo_root / ".agents" / "skills" / "nee-transcript-synthesis" / "SKILL.md"
    assert skill_path.exists(), "SKILL.md must exist"
    skill_text = skill_path.read_text()
    assert "Ear-First Spoken Voice Standard" in skill_text
    assert "Few-Shot Spoken Calibration Examples" in skill_text
    assert "Serial Number Accuracy" in skill_text
    assert "1,800-Image Scale Intuition" in skill_text
