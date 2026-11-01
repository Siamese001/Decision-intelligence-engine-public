"""
tests/test_stage03_anti_conflation_gate.py

Unit test suite for Wave 3:
1. Master Constraint C-28 presence in authoritative prompt files.
2. Anti-conflation validation failing closed on compound workstream entities, slugs, and table headers with ERR_COMPOUND_WORKSTREAM_CONFLATION.
3. Cameron Mercer's 09/18 macro reflection decomposition into isolated workstream evidence envelopes (workstream_project_beacon vs workstream_lens).
4. Exit gate (XExitGatesEvaluator) and Stage 03 post-processor enforcement.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.transcript_stages_core.anti_conflation import (
    ERR_COMPOUND_WORKSTREAM_CONFLATION,
    CompoundWorkstreamConflationError,
    decompose_multi_workstream_utterance,
    is_compound_workstream_reference,
    validate_anti_conflation,
)
from dual_pipeline.transcript_stages_core.graph_executor import (
    post_process_stage03_projection,
)
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


def test_master_constraint_c28_present_in_prompts():
    """Verify Master Constraint C-28 is declared in authoritative governance and stage prompts."""
    prompt_candidates = (
        Path("config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"),
        Path("config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_8.md"),
        Path("governance/decision_intelligence/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"),
    )
    gov_prompt_path = next((p for p in prompt_candidates if p.is_file()), prompt_candidates[0])
    gov_prompt = gov_prompt_path.read_text(encoding="utf-8")
    assert "C-28" in gov_prompt
    assert "Strict Entity-Workstream Disentanglement" in gov_prompt

    stage03_prompt = Path("config/prompts/synthesis/stages/03_graph_extractor.md").read_text(encoding="utf-8")
    assert "C-28" in stage03_prompt
    assert "Strict Workstream Disentanglement" in stage03_prompt


def test_is_compound_workstream_reference():
    """Verify regex detection of compound workstream names and slugs."""
    assert is_compound_workstream_reference("Project LENS / Sherpa")
    assert is_compound_workstream_reference("(Project LENS / Sherpa)")
    assert is_compound_workstream_reference("Observed Field Reality (Project LENS / Sherpa)")
    assert is_compound_workstream_reference("workstream:lens-sherpa")
    assert is_compound_workstream_reference("workstream:lens_sherpa")
    assert is_compound_workstream_reference("workstream:sherpa_lens")
    assert is_compound_workstream_reference("system:lens/sherpa")
    assert is_compound_workstream_reference("Sherpa & LENS")

    # Clean non-compound references must pass
    assert not is_compound_workstream_reference("Project LENS")
    assert not is_compound_workstream_reference("Project Beacon")
    assert not is_compound_workstream_reference("workstream:lens")
    assert not is_compound_workstream_reference("workstream:project_beacon")


def test_synthetic_compound_workstream_fails_closed():
    """Verify projection with compound workstream fails closed with ERR_COMPOUND_WORKSTREAM_CONFLATION."""
    bad_projection = {
        "schema_version": "5.7",
        "meeting_id": "test-meeting",
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "workstream:lens-sherpa",
                    "type": "workstream",
                    "name": "Project LENS / Sherpa",
                    "mutation": "CONFIRM",
                }
            ]
        }
    }

    with pytest.raises(CompoundWorkstreamConflationError) as exc_info:
        validate_anti_conflation(bad_projection)

    assert ERR_COMPOUND_WORKSTREAM_CONFLATION in str(exc_info.value)


def test_synthetic_compound_table_header_fails_in_x_exit_gates():
    """Verify XExitGatesEvaluator fails closed when recap contains compound workstream header."""
    clean_graph_json = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "test-meeting",
        "classification": "internal",
        "audiences": ["engineering"],
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:lens", "type": "system", "name": "LENS", "mutation": "CONFIRM"}
            ]
        },
        "evidence_index": {
            "ev-01": {"statement_id": "ev-01", "text": "LENS is ready", "exact_text": "LENS is ready"}
        },
        "evidence_only_statement_ids": []
    })

    bad_recap = (
        "## Meeting Objective\nAlign on delivery.\n\n"
        "### Observed Field Reality (Project LENS / Sherpa)\n"
        "| Role | Expected Capacity | Observed Field Reality (Project LENS / Sherpa) |\n"
        "| Engagement PM | Active | PartnerAdvisory Delivery Lead |\n\n"
        "## Next Steps & Action Items\n"
        "| # | Description | Owner | Target Date |\n"
        "| 1 | Complete LENS phase 1 | Jen Pan | 10/15/2026 |\n"
    )

    evaluator = XExitGatesEvaluator()
    with pytest.raises(CompoundWorkstreamConflationError) as exc_info:
        evaluator.execute(bad_recap, clean_graph_json, {"meeting_name": "Test"})

    assert ERR_COMPOUND_WORKSTREAM_CONFLATION in str(exc_info.value)


def test_post_process_stage03_projection_rejects_compound_entities():
    """Verify post_process_stage03_projection enforces anti-conflation gate."""
    bad_projection = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "test-meeting",
        "classification": "internal",
        "audiences": ["all"],
        "graph_mutations": {
            "entity_upserts": [
                {"id": "workstream:lens_sherpa", "type": "workstream", "name": "LENS / Sherpa", "mutation": "CONFIRM"}
            ]
        }
    })

    with pytest.raises(CompoundWorkstreamConflationError) as exc_info:
        post_process_stage03_projection(
            raw_graph_json=bad_projection,
            metadata={"meeting_name": "Test"},
            forensic_transcript="Transcript sample",
        )

    assert ERR_COMPOUND_WORKSTREAM_CONFLATION in str(exc_info.value)


def test_stephan_marceau_macro_reflection_decomposition():
    """
    Test feeding Cameron Mercer's 09/18 quote extracts two separate,
    partitioned evidence envelopes with strictly 0 compound entities.
    """
    quote_0918 = (
        "Whether that's the Sherpa team in clay, whether that's stuff that "
        "we're seeing on their NEA teams like the Lens team and Alex and whatnot."
    )

    envelopes = decompose_multi_workstream_utterance(
        utterance=quote_0918,
        speaker="Cameron Mercer",
        speaker_org="PartnerAdvisory",
    )

    assert len(envelopes) == 2

    # Envelope 1: Project Beacon
    env_sherpa = next(e for e in envelopes if e["workstream_id"] == "workstream_project_beacon")
    assert env_sherpa["workstream_name"] == "Project Beacon"
    assert env_sherpa["compound_entity_count"] == 0
    assert "Jen Pan" in env_sherpa["excluded_personnel"]
    assert "Clay Palumbo" in env_sherpa["associated_personnel"]

    # Envelope 2: Project LENS
    env_lens = next(e for e in envelopes if e["workstream_id"] == "workstream_lens")
    assert env_lens["workstream_name"] == "Project LENS"
    assert env_lens["compound_entity_count"] == 0
    assert "Jen Pan" in env_lens["associated_personnel"]
    assert "Alex Sanchez" in env_lens["associated_personnel"]
    assert "Clay Palumbo" in env_lens["excluded_personnel"]

    # Ensure zero compound entities across all extracted envelopes
    total_compound_entities = sum(e["compound_entity_count"] for e in envelopes)
    assert total_compound_entities == 0
