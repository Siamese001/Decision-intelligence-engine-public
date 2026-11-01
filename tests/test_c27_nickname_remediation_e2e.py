"""
tests/test_c27_nickname_remediation_e2e.py

End-to-end and lifecycle verification for C-27 nickname self-correction grounding,
Project LENS entity clarifications, surgical remediation of Output 01 C-27 defects,
and DeterministicHealer preservation of Ambiguity Lock Register grounding rows.
"""
from __future__ import annotations

from typing import Any, Dict, List

from dual_pipeline.canonical_pipeline.c0_pa_runner import _ensure_lens_entity_clarifications
from dual_pipeline.identity_resolver_core.resolver import IdentityResolver
from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.transcript_qa_core.gate1_entity_context import (
    evaluate_referenced_entity_context_gate,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer


class _StubResolver:
    """Hermetic stub IdentityResolver for deterministic C-27 gate tests."""

    def __init__(self, first_names: set[str], first_name_index: Dict[str, List[Dict[str, Any]]] | None = None) -> None:
        self._first_names = first_names
        self.first_name_index = first_name_index or {
            "mandy": [{"canonical_name": "harper Hoffman", "organization": "NEA", "role": "Product Manager"}],
            "amanda": [
                {"canonical_name": "harper Hoffman", "organization": "NEA", "role": "Product Manager"},
                {"canonical_name": "Amanda Other", "organization": "NEA", "role": "Analyst"},
            ],
            "garrett": [{"canonical_name": "Garrett Smith", "organization": "NEA", "role": "Engineer"}],
        }

    def get_first_names_requiring_grounding(
        self, attendees: List[str] | None = None, project: str | None = None
    ) -> set[str]:
        return set(self._first_names)


def test_e2e_lens_entity_clarifications_and_self_correction_pass_gate1_10() -> None:
    """Verify Project LENS clarifications include 'amanda' -> 'harper Hoffman' and 'Amanda, Mandy one on one' passes Gate 1.10."""
    meta: Dict[str, Any] = {"project": "LENS", "attendees": ["Amit Ayer", "Alex Sanchez"]}
    _ensure_lens_entity_clarifications(meta)

    tokens = {c["token"]: c["canonical_name"] for c in meta["entity_clarifications"]}
    assert tokens.get("mandy") == "harper Hoffman"
    assert tokens.get("amanda") == "harper Hoffman"

    f1_text = (
        "# Forensic Transcript\n\n"
        "- **Date:** 10/27/2026\n"
        "- **Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory)\n\n"
        "### [00:04:12]\n"
        "**Amit Ayer:** We talked about this in the Amanda, Mandy one on one yesterday.\n\n"
        "## Attribution Summary\n\n"
        "| Speaker | Turns | Words | Share | Confidence |\n"
        "|---|---|---|---|---|\n"
        "| Amit Ayer | 1 | 13 | 100% | High |\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Ambiguous / Corrupted Token | Turn / Timestamp | Statement ID | Resolved Canonical Grounding | Verification Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Mandy | [00:04:12] | stmt-001 | harper Hoffman (NEA, Product Manager) | Roster |\n"
    )
    f2_text = (
        "# Executive Recap\n\n"
        "Amit Ayer noted insights from the harper Hoffman one-on-one session.\n"
    )

    resolver = _StubResolver({"amanda", "mandy", "garrett"})
    passed, failures = evaluate_referenced_entity_context_gate(
        f1_text=f1_text,
        f2_text=f2_text,
        roster_names=["Amit Ayer", "Alex Sanchez"],
        entity_clarifications=meta["entity_clarifications"],
        identity_resolver=resolver,
        project="LENS",
    )
    assert passed is True, f"Expected Gate 1.10 to pass, got failures: {failures}"
    assert failures == []


def test_e2e_stage01_surgical_remediation_and_healer_preserve_c27_alr_grounding() -> None:
    """Verify C-27 failure on Stage 01 triggers SurgicalRemediator, adds ALR row, survives DeterministicHealer, and passes Gate 1.10."""
    initial_f1 = (
        "# Forensic Transcript\n\n"
        "- **Date:** 10/27/2026\n"
        "- **Attendees:** Amit Ayer (PartnerAdvisory), Alex Sanchez (PartnerAdvisory)\n\n"
        "### [00:05:10]\n"
        "**Amit Ayer:** Let us check with Garrett before finalizing the conduit deployment.\n\n"
        "## Attribution Summary\n\n"
        "| Speaker | Turns | Words | Share | Confidence |\n"
        "|---|---|---|---|---|\n"
        "| Amit Ayer | 1 | 11 | 100% | High |\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Ambiguous / Corrupted Token | Turn / Timestamp | Statement ID | Resolved Canonical Grounding | Verification Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Amit Ayer | [00:05:10] | stmt-001 | Amit Ayer (PartnerAdvisory) | Acoustic |\n"
    )
    resolver = _StubResolver({"garrett"})
    passed_initial, failures_initial = evaluate_referenced_entity_context_gate(
        f1_text=initial_f1,
        f2_text="",
        roster_names=["Amit Ayer", "Alex Sanchez"],
        entity_clarifications=[],
        identity_resolver=resolver,
        project="LENS",
    )
    assert passed_initial is False
    assert any("C27_UNGROUNDED_STAKEHOLDER_IN_TRANSCRIPT" in f and "'Garrett'" in f for f in failures_initial)

    qa_report = {
        "output_01_forensic_transcript": {
            "passed": False,
            "failures": failures_initial,
        }
    }
    meta: Dict[str, Any] = {
        "project": "LENS",
        "date": "10/27/2026",
        "attendees": ["Amit Ayer", "Alex Sanchez"],
    }

    repaired, did_remediate, audit = SurgicalRemediator.remediate_output_1(
        transcript_text=initial_f1,
        qa_report=qa_report,
        judge_report={},
        directive="",
        metadata=meta,
    )
    assert did_remediate is True, f"Expected surgical remediation to succeed, audit={audit}"
    assert 'In-dialogue reference to "Garrett"' in repaired

    healed, _ = DeterministicHealer.heal_output_1(repaired, metadata=meta)
    assert 'In-dialogue reference to "Garrett"' in healed

    passed_after, failures_after = evaluate_referenced_entity_context_gate(
        f1_text=healed,
        f2_text="",
        roster_names=["Amit Ayer", "Alex Sanchez"],
        entity_clarifications=[],
        identity_resolver=resolver,
        project="LENS",
    )
    assert passed_after is True, f"Expected Gate 1.10 to pass after surgical repair + healing, got: {failures_after}"


def test_e2e_forensic_normalizer_native_retry_applies_c27_remediation() -> None:
    """Verify ForensicNormalizer._execute_native applies SurgicalRemediator using last_qa_report on retry."""
    raw_source = (
        "[00:01:00] SPEAKER_00: We need Garrett to review the schema.\n"
        "[00:01:15] SPEAKER_01: Agreed, I will message him today.\n"
    )
    meta: Dict[str, Any] = {
        "project": "LENS",
        "meeting_name": "Schema Sync",
        "date": "10/27/2026",
        "attendees": ["Amit Ayer", "Alex Sanchez"],
        "speaker_map": {"SPEAKER_00": "Amit Ayer", "SPEAKER_01": "Alex Sanchez"},
        "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
        "last_qa_report": {
            "output_01_forensic_transcript": {
                "passed": False,
                "failures": [
                    "C27_UNGROUNDED_STAKEHOLDER_IN_TRANSCRIPT: Line 12 references 'Garrett' without full name, organization, or title context. Enforce C-27 grounding."
                ],
            }
        },
    }
    u0_context = {
        "raw_text": raw_source,
        "clean_transcript": raw_source,
        "metadata": meta,
        "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
    }
    normalizer = ForensicNormalizer()
    out_1 = normalizer.execute(u0_context=u0_context, plan={}, provider=None, remediation_directive=None)
    healed_1, _ = DeterministicHealer.heal_output_1(out_1, metadata=meta)

    assert 'In-dialogue reference to "Garrett"' in healed_1
    resolver = _StubResolver({"garrett"})
    passed, failures = evaluate_referenced_entity_context_gate(
        f1_text=healed_1,
        f2_text="",
        roster_names=["Amit Ayer", "Alex Sanchez"],
        entity_clarifications=[],
        identity_resolver=resolver,
        project="LENS",
    )
    assert passed is True, f"Expected Gate 1.10 to pass after native retry remediation, got: {failures}"


def test_base_identity_resolver_maps_amanda_to_mandy_hopmann() -> None:
    """Verify IdentityResolver base catalogue includes Amanda as an alias for harper Hoffman."""
    resolver = IdentityResolver()
    assert "amanda" in resolver.first_name_index
    canonical_names = {entry.get("canonical_name") for entry in resolver.first_name_index["amanda"]}
    assert "harper Hoffman" in canonical_names
