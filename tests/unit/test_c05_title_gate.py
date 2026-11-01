"""Unit tests for Wave 2: C0.5 Mandatory Title Completeness Gate and prior context invalidation."""

import pytest

from dual_pipeline.transcript_pipeline_core.context_builder import load_prior_meeting_context
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    apply_hitl_role_resolution,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


def test_c05_gate_passes_when_all_titles_resolved():
    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Taylor Mercer", "role": "Delivery Lead / PM", "org": "PartnerAdvisory"},
                {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
                {"name": "Daniel Brankin", "role": "Director of Digital Technology", "org": "Apex Global Enterprises (NEA)"},
            ]
        }
    }
    gate = C05TitleCompletenessGate()
    result = gate.execute(l0_context)
    assert result.get("c05_title_gate_cleared") is True


def test_c05_gate_raises_hitl_on_placeholder_or_empty_title():
    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Unregistered Person", "role": "", "org": "Apex Global Enterprises (NEA)"},
                {"name": "Sharon Diaram", "role": "APEX", "org": "Apex Global Enterprises (NEA)"},
            ]
        }
    }
    gate = C05TitleCompletenessGate()
    with pytest.raises(HITLRequiredError) as exc_info:
        gate.execute(l0_context)

    err = exc_info.value
    assert err.hitl_type == "role_clarification"
    assert "Unregistered Person" in err.prompt
    assert "role:Unregistered Person" in err.missing_fields
    assert len(err.options) >= 3
    # Check PL-2: invalidate_prior_outputs must be set
    assert l0_context["metadata"].get("invalidate_prior_outputs") is True


def test_c05_gate_resolves_after_operator_hitl_application(tmp_path):
    l0_context = {
        "metadata": {
            "roster": [
                {"name": "Unregistered Person", "role": "", "org": "Apex Global Enterprises (NEA)"},
            ]
        }
    }
    gate = C05TitleCompletenessGate()
    with pytest.raises(HITLRequiredError):
        gate.execute(l0_context)

    # Operator provides resolution
    receipt = apply_hitl_role_resolution(
        l0_context["metadata"],
        "Unregistered Person",
        "Director of Digital Technology",
        repo_root=tmp_path,
    )
    assert receipt["hitl_receipt_bound"] is True
    assert receipt["resolved_role"] == "Director of Digital Technology"

    # Subsequent execution clears gate
    result = gate.execute(l0_context)
    assert result.get("c05_title_gate_cleared") is True


def test_prior_context_skipped_when_invalidate_prior_outputs_set(tmp_path):
    out_dir = tmp_path / "meeting_out"
    out_dir.mkdir(parents=True)
    f1 = out_dir / "01_test_hitl_resolved_transcript.md"
    f1.write_text("# Meeting\nTurn 1: hello", encoding="utf-8")

    meta = {"meeting_name": "test_meeting", "invalidate_prior_outputs": True}
    load_prior_meeting_context(str(out_dir), meta)
    assert "prior_forensic_transcript" not in meta
    assert meta.get("has_prior_meeting_artifacts") is not True
