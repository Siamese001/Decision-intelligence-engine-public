from __future__ import annotations

from dual_pipeline.canonical_pipeline.lifecycle import _extract_next_directive
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.transcript_stages_core.graph_post_extractor import (
    _ensure_critical_architecture_systems,
)
from dual_pipeline.transcript_stages_core.recap_executor import _post_process_recap


def test_ensure_critical_architecture_systems_prunes_unmentioned_lens():
    """Verify system:lens is NOT injected when LENS is absent from evidence and source text."""
    mutations = {"entity_upserts": []}
    ev_index = {
        "ev-01": {
            "speaker": "Jill Cizewski",
            "text": "Jill discussed the project development specialist role.",
            "source_classification": "FACT",
        }
    }
    source_text = "Jill Cizewski and Daniel Brankin discussed business case updates."

    _ensure_critical_architecture_systems(mutations, ev_index, source_text=source_text)

    entity_ids = [e["id"] for e in mutations["entity_upserts"]]
    assert "system:lens" not in entity_ids, "system:lens should NOT be injected when unmentioned"


def test_ensure_critical_architecture_systems_injects_when_lens_mentioned():
    """Verify system:lens IS injected when LENS is present in evidence or source text."""
    mutations = {"entity_upserts": []}
    ev_index = {
        "ev-01": {
            "speaker": "Daniel Brankin",
            "text": "Daniel discussed the LENS platform and model evaluation.",
            "source_classification": "FACT",
        }
    }
    source_text = "Daniel evaluated LENS platform capabilities."

    _ensure_critical_architecture_systems(mutations, ev_index, source_text=source_text)

    entity_ids = [e["id"] for e in mutations["entity_upserts"]]
    assert "system:lens" in entity_ids, "system:lens should be injected when mentioned in evidence"


def test_recap_executor_deletes_tone_arc_on_remediation():
    """Verify recap post-processor strips Meeting Tone Arc when remediation_directive mentions tone."""
    raw_recap = (
        "## Meeting Header\n"
        "**Date:** 09/28/2026\n\n"
        "## Meeting Tone Arc & Strategic Dynamics\n"
        "The meeting had high energy and collaborative tone.\n\n"
        "## Key Updates\n"
        "- Discussion on business case.\n"
    )
    metadata = {
        "remediation_directive": "TARGETED REMEDIATION REQUIRED FOR OUTPUT_2:\nRemove unsupported engagement and Meeting Tone Arc dead weight.",
        "archetype": "COLLABORATIVE_WORKING_SESSION",
        "date": "09/28/2026",
    }

    healed = _post_process_recap(raw_recap, metadata=metadata, forbid_sections=[])
    assert "Meeting Tone Arc" not in healed, "Tone arc section should be stripped on remediation"
    assert "Discussion on business case" in healed, "Substantive updates must be preserved"


def test_extract_next_directive_multi_artifact_propagation():
    """Verify _extract_next_directive populates metadata and combines directives when multiple outputs fail."""
    eval_result = QAEvaluationResult(
        passed=False,
        authoritative_write_authorized=False,
        qa_report={},
        judge_report={
            "output_1_evaluation": {"status": "PASS"},
            "output_2_evaluation": {
                "status": "FAIL",
                "actionable_remediation_diff": "Strip Meeting Tone Arc dead weight.",
                "forensic_rationale": "Tone arc contains unsupported engagement.",
            },
            "output_3_evaluation": {
                "status": "FAIL",
                "actionable_remediation_diff": "Prune hallucinated system:lens.",
                "forensic_rationale": "LENS was never mentioned in the transcript.",
            },
        },
        final_decision={
            "status": "FAIL",
            "output_1_pass": True,
            "output_2_pass": False,
            "output_3_pass": False,
        },
    )
    metadata = {}
    directive = _extract_next_directive(eval_result, metadata=metadata)

    assert directive is not None
    assert "TARGETED REMEDIATION REQUIRED FOR OUTPUT_2" in directive
    assert "TARGETED REMEDIATION REQUIRED FOR OUTPUT_3" in directive
    assert "remediation_directive_recap" in metadata
    assert "remediation_directive_graph" in metadata
    assert "Meeting Tone Arc" in metadata["remediation_directive_recap"]
    assert "system:lens" in metadata["remediation_directive_graph"]
    assert metadata["remediation_directive"] == directive
