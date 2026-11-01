"""
tests/test_c27_nickname_remediation_adversarial.py

Adversarial and edge-case tests covering:
1. Boundary payloads (empty transcripts, missing ALR sections, empty critique payloads).
2. Malformed inputs (lowercase-only directives, malformed QA reports, disallowed tokens).
3. Exact production regression scenarios ("Amanda, Mandy one on one", "Ed, Eduardo",
   unclarified standalone bare names still failing closed under C-27).
"""
from __future__ import annotations

from typing import Any, Dict, List

from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
    SurgicalRemediator,
    _extract_critique_alr_token,
    _target_for_output_1,
)
from dual_pipeline.transcript_qa_core.gate1_entity_context import (
    _is_bare_name_ungrounded,
    evaluate_referenced_entity_context_gate,
)


class _AdversarialResolver:
    def __init__(self, names: set[str]) -> None:
        self._names = names
        self.first_name_index: Dict[str, List[Dict[str, Any]]] = {
            "amanda": [
                {"canonical_name": "harper Hoffman"},
                {"canonical_name": "Amanda Separate"},
            ],
            "mandy": [{"canonical_name": "harper Hoffman"}],
            "eduardo": [{"canonical_name": "Eduardo (Ed) Juan More"}],
            "ed": [{"canonical_name": "Eduardo (Ed) Juan More"}],
            "nicholas": [{"canonical_name": "Nicholas Cage"}],
        }

    def get_first_names_requiring_grounding(
        self, attendees: List[str] | None = None, project: str | None = None
    ) -> set[str]:
        return set(self._names)


def test_adversarial_boundary_empty_and_whitespace_inputs() -> None:
    """Category 1 (Boundary payload): Empty and whitespace-only transcripts fail gracefully without crashing."""
    out, did_rem, audit = SurgicalRemediator.remediate_output_1(
        transcript_text="   ",
        qa_report={"output_01_forensic_transcript": {"failures": ["C27_UNGROUNDED_STAKEHOLDER_IN_TRANSCRIPT"]}},
        judge_report={},
    )
    assert did_rem is False
    assert audit == ["Empty transcript text"]

    resolver = _AdversarialResolver({"amanda"})
    passed, failures = evaluate_referenced_entity_context_gate(
        f1_text="",
        f2_text="",
        roster_names=[],
        entity_clarifications=[],
        identity_resolver=resolver,
    )
    assert passed is True
    assert failures == []


def test_adversarial_malformed_qa_report_and_disallowed_tokens() -> None:
    """Category 2 (Malformed input): Non-dict QA reports, missing keys, and disallowed tokens do not corrupt ALR."""
    assert _target_for_output_1(None, None) is None  # type: ignore[arg-type]
    assert _target_for_output_1({"output_01_forensic_transcript": "invalid"}, {}) is None

    # Disallowed generic words must never be extracted as stakeholder names
    for bad_word in ("Speakers", "Dialogue", "Reference", "Chachi", "ChatGPT"):
        critique = f"C27_UNGROUNDED_STAKEHOLDER_IN_TRANSCRIPT: Line 10 references '{bad_word}' without context."
        assert _extract_critique_alr_token(critique, "") is None

    # Duplicate token already in ALR must not be re-added
    existing_alr = '| 2 | In-dialogue reference to "Amanda" | Line 97 | stmt-alr-002 | Clarified | Audit |\n'
    critique_dup = "C27_UNGROUNDED_STAKEHOLDER_IN_TRANSCRIPT: Line 97 references 'Amanda' without full name."
    assert _extract_critique_alr_token(critique_dup, existing_alr) is None


def test_adversarial_production_regression_self_correction_vs_truly_ungrounded() -> None:
    """Category 3 (Exact production regression): Spoken self-corrections pass while standalone ungrounded names still fail closed."""
    # Spoken self-corrections across punctuation variants pass
    for line in (
        "**Amit Ayer:** In my Amanda, Mandy one on one we covered this.",
        "**Amit Ayer:** We asked Amanda — Mandy to review the charter.",
        "**Amit Ayer:** Check with Amanda or Mandy before tomorrow.",
        "**Amit Ayer:** I spoke with Amanda, I mean Mandy, yesterday.",
        "**Amit Ayer:** Ed, Eduardo confirmed the transformer telemetry.",
    ):
        assert _is_bare_name_ungrounded(line, "amanda", exempt_tokens={"mandy"}) is False, f"Failed on: {line}"
        assert _is_bare_name_ungrounded(line, "ed", exempt_tokens={"eduardo"}) is False, f"Failed on: {line}"

    # Standalone ungrounded mention of a different person (or Amanda without Mandy and without clarification) MUST fail closed
    ungrounded_line = "**Amit Ayer:** I talked to Nicholas yesterday about the budget."
    assert _is_bare_name_ungrounded(ungrounded_line, "nicholas", exempt_tokens={"mandy"}) is True

    # Amanda followed by an unrelated person name (e.g. "Amanda, Nicholas") MUST still fail closed as ungrounded!
    unrelated_pair_line = "**Amit Ayer:** I asked Amanda, Nicholas about the kickoff."
    assert _is_bare_name_ungrounded(unrelated_pair_line, "amanda", exempt_tokens=set()) is True
